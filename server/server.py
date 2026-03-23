"""Core Flask server for IFC processing with pluggable data store backends"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, jsonify, Response, has_request_context
from flask_cors import CORS


_SHA_REF_RE = re.compile(r'^[0-9a-fA-F]{7,40}$')


def _load_env_file(path):
    """Load simple KEY=VALUE entries from a .env file into os.environ.

    Existing environment variables are preserved.
    """
    if not os.path.isfile(path):
        return

    try:
        with open(path, 'r', encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue

                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()

                # Remove optional single/double quotes around values.
                if ((value.startswith('"') and value.endswith('"')) or
                        (value.startswith("'") and value.endswith("'"))):
                    value = value[1:-1]

                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f"[WARN] Failed to load env file {path}: {e}")


def _load_env():
    """Load .env from likely locations.

    Priority is current process environment, then file values.
    """
    server_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(server_dir)

    # Allow both server/.env and repo-root/.env.
    _load_env_file(os.path.join(server_dir, '.env'))
    _load_env_file(os.path.join(repo_root, '.env'))


_load_env()

# Add ingestors to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'ingestors'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'dataStores', 'fileBased'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'dataStores', 'mongodbBased'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils', 'ifc_utils'))

# Debug logging to file
DEBUG_LOG = None

def debug_print(msg):
    """Print to both stdout and debug log file"""
    global DEBUG_LOG
    print(msg, flush=True)
    if DEBUG_LOG:
        try:
            with open(DEBUG_LOG, 'a', encoding='utf-8') as f:
                f.write(msg + '\n')
        except:
            pass

from ifc4ingestor import IFC2JSONSimple
from github_ifc import (
    GitHubFetchError,
    GitHubFileNotFoundError,
    GitHubModelRef,
    fetch_ifc_bytes,
    parse_github_model_url,
)
from github_proxy import (
    GitHubProxyError,
    fetch_file_bytes,
    get_commit_details,
    get_repo_tree,
    list_branches as proxy_list_branches,
    list_commits as proxy_list_commits,
)
from ifc_diff_service import diff_ifc_bytes, summarize_ifc_bytes
from indexed_artifacts import IndexedArtifactStore
from preindex_service import DemoPreindexService


class IFCProcessingServer:
    """Core IFC Processing Server with pluggable data store backends"""
    
    def __init__(self, data_store_type='fileBased'):
        """Initialize the server with specified data store backend
        
        Args:
            data_store_type: 'fileBased' or 'mongodbBased'
        """
        self.data_store_type = data_store_type
        self.app = Flask(__name__)
        self.file_store = None
        self.memory_tree = None
        self._descendants_exporter = None
        self.git_manager = None
        self.indexed_artifacts = None
        self.preindex_service = None
        
        # Configure Flask app
        self._configure_app()
        
        # Initialize data store and memory tree based on type
        self._initialize_backend()
        
        # Register routes
        self._register_routes()
    
    def _configure_app(self):
        """Configure Flask application"""
        # Enable CORS for all routes
        CORS(self.app)
        
        # Configuration
        if os.getenv('VERCEL'):
            # Vercel functions can only write inside /tmp at runtime.
            upload_folder = os.path.join('/tmp', 'hackporto', 'uploads')
        else:
            upload_folder = os.path.join(os.path.dirname(__file__), 'uploads')
        ALLOWED_EXTENSIONS = {'ifc', 'json'}
        MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB max file size
        
        self.app.config['UPLOAD_FOLDER'] = upload_folder
        self.app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
        self.app.config['DATA_STORE_TYPE'] = self.data_store_type
        self.app.config['ALLOWED_EXTENSIONS'] = ALLOWED_EXTENSIONS
        
        # Ensure upload folder exists
        os.makedirs(upload_folder, exist_ok=True)
        
        # Store config for use in route handlers
        self.upload_folder = upload_folder
        self.allowed_extensions = ALLOWED_EXTENSIONS
    
    def _initialize_backend(self):
        """Initialize the selected data store backend"""
        if self.data_store_type == 'fileBased':
            from fileBased import FileBasedStore
            from memoryTree import MemoryTree

            if os.getenv('VERCEL'):
                store_path = os.path.join('/tmp', 'hackporto', 'data')
                os.makedirs(store_path, exist_ok=True)
            else:
                store_path = None
            
            self.file_store = FileBasedStore(base_path=store_path)
            self.memory_tree = MemoryTree()
            
            # Refresh memory tree on startup
            self._refresh_memory_tree()
            print(f"[OK] Initialized file-based data store at: {self.file_store.base_path}")
            self._init_git_versioning()
            self._init_preindexing()

        elif self.data_store_type == 'mongodbBased':
            from mongodbBased import MongoDBStore
            from mongodbMemoryTree import MongoDBMemoryTree
            
            self.file_store = MongoDBStore()
            self.memory_tree = MongoDBMemoryTree()
            
            print(f"✅ Initialized MongoDB data store")
        else:
            raise ValueError(f"Unknown data store type: {self.data_store_type}")
    
    def _init_git_versioning(self):
        """Initialise GitVersionManager for the file-based backend."""
        try:
            from git_versioning import GitVersionManager
            repo_root = os.environ.get(
                'VERSION_REPO_ROOT',
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            data_rel_path = os.environ.get(
                'VERSION_DATA_REL_PATH',
                'server/dataStores/fileBased/data'
            )
            push_remote_url = os.environ.get('GIT_PUSH_REMOTE_URL')
            push_branch = os.environ.get('GIT_PUSH_BRANCH')
            self.git_manager = GitVersionManager(
                repo_root=repo_root,
                memory_tree_class=type(self.memory_tree),
                data_rel_path=data_rel_path,
                push_remote_url=push_remote_url,
                push_branch=push_branch
            )
            if self.git_manager._git_available:
                sha = self.git_manager.get_latest_sha()
                print(f"[OK] Git versioning enabled. HEAD: {sha[:8] if sha else 'none'}")
                if push_remote_url:
                    print(f"[OK] Push target: {push_remote_url} ({push_branch or 'main'})")
            else:
                print('[WARN] Git versioning: git not available or not a repository')
        except Exception as e:
            print(f'[WARN] Git versioning unavailable: {e}')
            self.git_manager = None

    def _init_preindexing(self):
        """Initialise additive demo preindexing."""
        try:
            artifact_path = os.environ.get("PREINDEX_ARTIFACTS_PATH")
            artifact_backend = os.environ.get("PREINDEX_STORAGE_BACKEND")
            blob_prefix = os.environ.get("PREINDEX_BLOB_PREFIX")
            max_workers = int(os.environ.get("PREINDEX_MAX_WORKERS", "1"))
            self.indexed_artifacts = IndexedArtifactStore(
                memory_tree_class=type(self.memory_tree),
                base_path=artifact_path,
                backend=artifact_backend,
                blob_prefix=blob_prefix,
            )
            self.preindex_service = DemoPreindexService(
                self.indexed_artifacts,
                github_token_resolver=self._resolve_github_token,
                max_workers=max_workers,
            )

            if not self.preindex_service.is_enabled():
                print("[OK] Demo preindex disabled (no tracked manifest configured)")
                return

            auto_start_default = "false" if os.getenv("VERCEL") else "true"
            auto_start = os.environ.get("PREINDEX_AUTOSTART", auto_start_default).lower() not in (
                "0",
                "false",
                "no",
            )
            if auto_start:
                summary = self.preindex_service.enqueue_manifest(force=False)
                print(f"[OK] Demo preindex scheduled: {summary}")
            else:
                print("[OK] Demo preindex autostart disabled for this runtime")
        except Exception as e:
            print(f"[WARN] Demo preindex unavailable: {e}")
            self.indexed_artifacts = None
            self.preindex_service = None

    def _get_memory_tree(self, version=None):
        """Return the MemoryTree for *version* (a git SHA), or the live tree.

        Raises ValueError for invalid or unresolvable version strings.
        """
        if not version or version.lower() in ('latest', 'head'):
            return self.memory_tree
        if self.preindex_service:
            preindexed_tree = self.preindex_service.load_memory_tree(version)
            if preindexed_tree is not None:
                return preindexed_tree
            if self.indexed_artifacts:
                preindexed_entry = self.indexed_artifacts.get_entry(version)
                if preindexed_entry and preindexed_entry.get("state") != "ready":
                    raise ValueError(
                        f"Indexed version {version!r} is currently "
                        f"{preindexed_entry.get('state') or 'unavailable'}."
                    )
        if self.git_manager is None:
            raise ValueError('Git versioning is not available for this backend')
        if not self.git_manager.is_valid_sha(version):
            raise ValueError(
                f'Invalid version format: {version!r}. '
                'Expected a 7-40 character hex git commit SHA.'
            )
        return self.git_manager.get_memory_tree_for_version(version)

    def _refresh_memory_tree(self):
        """Refresh the in-memory component tree"""
        try:
            if self.data_store_type == 'fileBased':
                self.memory_tree.refresh_from_store(self.file_store.base_path)
                models = self.memory_tree.get_models()
                print(f"✅ Memory tree refreshed: {len(models)} model(s) loaded")
                return len(models)
            else:
                # MongoDB backend handles its own refresh
                return self.memory_tree.refresh()
        except Exception as e:
            print(f"❌ Error refreshing memory tree: {e}")
            return 0

    def _expand_entity_types_for_models(self, entity_types, models, tree=None):
        """Expand entity types to include all descendants, filtered by model."""
        if not entity_types:
            return {}

        print(f"[EXPAND] Input: entity_types={entity_types}, models={models}")
        
        mt = tree or self.memory_tree
        search_models = models if models else mt.get_models()
        descendants = set()

        try:
            if self._descendants_exporter is None:
                try:
                    from ifc_descendants_export import IFCDescendantsExporter
                    self._descendants_exporter = IFCDescendantsExporter()
                    print("[EXPAND] Descendants exporter initialized")
                except (Exception, SystemExit) as e:
                    print(f"[WARN] IFC descendants exporter unavailable: {e}")
                    self._descendants_exporter = None

            if self._descendants_exporter is None:
                descendants = set(entity_types)
                print(f"[EXPAND] No exporter, using fallback: {descendants}")
                raise RuntimeError("Descendants exporter unavailable")

            for entity_type in entity_types:
                entity_descendants = self._descendants_exporter.get_descendants(entity_type)
                print(f"[EXPAND] {entity_type} -> {entity_descendants}")
                descendants.update(entity_descendants)
        except Exception as e:
            print(f"[WARN] Descendant expansion failed: {e}")
            descendants = set(entity_types)

        if not descendants:
            descendants = set(entity_types)

        print(f"[EXPAND] Final descendants: {descendants}")
        
        per_model = {}
        for model_name in search_models:
            model_types = set(mt.get_entity_types(models=[model_name]))
            intersection = model_types.intersection(descendants)
            per_model[model_name] = sorted(list(intersection))
            print(f"[EXPAND] Model {model_name}: available={len(model_types)}, intersection={per_model[model_name]}")

        return per_model
    
    def _expand_component_types_for_models(self, component_types, models, tree=None):
        """Expand component types to include all descendants, filtered by model.
        
        Component types are stored WITHOUT the "Component" suffix (e.g., IfcWall, IfcWallStandardCase).
        The user can query with or without "Component" suffix - both will work.
        """
        if not component_types:
            return {}

        print(f"\n🔍 _expand_component_types_for_models:")
        print(f"   Input component_types: {component_types}")

        mt = tree or self.memory_tree
        search_models = models if models else mt.get_models()
        descendants = set()

        try:
            if self._descendants_exporter is None:
                try:
                    from ifc_descendants_export import IFCDescendantsExporter
                    self._descendants_exporter = IFCDescendantsExporter()
                except (Exception, SystemExit) as e:
                    print(f"⚠️  IFC descendants exporter unavailable: {e}")
                    self._descendants_exporter = None

            if self._descendants_exporter is None:
                # If exporter unavailable, return component types as-is (stripped)
                descendants = set()
                for comp_type in component_types:
                    # Strip "Component" suffix if present
                    if comp_type.endswith('Component'):
                        descendants.add(comp_type[:-9])
                    else:
                        descendants.add(comp_type)
                print(f"   ⚠️  Exporter unavailable, using fallback: {descendants}")
                raise RuntimeError("Descendants exporter unavailable")

            # Get descendants for each component type
            for comp_type in component_types:
                # Strip "Component" suffix if present to get the entity type name
                entity_type = comp_type
                if entity_type.endswith('Component'):
                    entity_type = entity_type[:-9]
                
                print(f"   Processing component type '{comp_type}' → entity type '{entity_type}'")
                
                # Get descendants of the entity type (these are already without Component suffix)
                entity_descendants = self._descendants_exporter.get_descendants(entity_type)
                print(f"   Found descendants: {entity_descendants}")
                descendants.update(entity_descendants)
        except Exception as e:
            print(f"⚠️  Component type expansion failed: {e}")
            # Fallback: strip Component suffix and use as-is
            descendants = set()
            for comp_type in component_types:
                if comp_type.endswith('Component'):
                    descendants.add(comp_type[:-9])
                else:
                    descendants.add(comp_type)
            print(f"   Using fallback descendants: {descendants}")

        if not descendants:
            # Fallback: just strip Component and use as-is
            descendants = set()
            for comp_type in component_types:
                if comp_type.endswith('Component'):
                    descendants.add(comp_type[:-9])
                else:
                    descendants.add(comp_type)
            print(f"   Descendants was empty, using fallback: {descendants}")

        print(f"   Final descendants to search: {descendants}")
        
        per_model = {}
        for model_name in search_models:
            model_types = set(mt.get_component_types(models=[model_name]))
            intersection = model_types.intersection(descendants)
            per_model[model_name] = sorted(list(intersection))
            print(f"   Model '{model_name}': available types {len(model_types)}, intersection {len(intersection)}: {per_model[model_name]}")

        return per_model
    
    def _allowed_file(self, filename):
        """Check if file extension is allowed"""
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in self.app.config.get('ALLOWED_EXTENSIONS', [])

    def _resolve_github_token(self, explicit_token=None):
        header_token = ''
        if has_request_context():
            header_token = request.headers.get('X-GitHub-Token') or ''
        token = (
            explicit_token
            or header_token
            or os.environ.get('GITHUB_TOKEN')
            or ''
        )
        return token.strip()

    def _parse_repo_query(self, require_ref=False, require_path=False):
        repo_owner = request.args.get('owner', '').strip()
        repo_name = request.args.get('repo', '').strip()
        ref_name = request.args.get('ref', '').strip() or request.args.get('sha', '').strip()
        file_path = request.args.get('path', '').strip()

        missing = []
        if not repo_owner:
            missing.append('owner')
        if not repo_name:
            missing.append('repo')
        if require_ref and not ref_name:
            missing.append('ref')
        if require_path and not file_path:
            missing.append('path')

        if missing:
            raise ValueError(f"Missing query params: {', '.join(missing)}")

        return repo_owner, repo_name, ref_name, file_path

    def _split_csv_arg(self, value):
        if not value:
            return None
        values = [item.strip() for item in value.split(',') if item.strip()]
        return values or None

    def _looks_like_git_sha(self, value):
        return bool(_SHA_REF_RE.match((value or '').strip()))

    def _model_management_enabled(self):
        if not os.getenv('VERCEL'):
            return True
        return os.environ.get('HACKPORTO_ALLOW_INTERNAL_MODEL_WRITES', '').lower() in ('1', 'true', 'yes')

    def _model_management_guard(self):
        if self._model_management_enabled():
            return None
        return jsonify({
            'error': 'Model management is internal-only while Vercel storage is ephemeral.',
            'storageMode': 'ephemeral',
            'modelManagementMode': 'internal-only',
        }), 403
    
    def _register_routes(self):
        """Register all Flask routes"""
        
        @self.app.route('/')
        def admin():
            """Serve the admin page"""
            return render_template('admin.html')
        
        @self.app.route('/viewer')
        def viewer():
            """Serve the advanced viewer page"""
            return render_template('viewer.html')
        
        @self.app.route('/api/upload', methods=['POST'])
        def upload_file():
            """Handle file upload and processing"""
            guard_response = self._model_management_guard()
            if guard_response:
                return guard_response
            try:
                overwrite = request.args.get('overwrite', 'false').lower() in ('1', 'true', 'yes')

                # Check if file is in request
                if 'file' not in request.files:
                    return jsonify({'error': 'No file provided'}), 400
                
                file = request.files['file']
                
                if file.filename == '':
                    return jsonify({'error': 'No file selected'}), 400
                
                if not self._allowed_file(file.filename):
                    return jsonify({'error': 'File type not allowed. Use .ifc or .json'}), 400
                
                # Secure the filename
                filename = secure_filename(file.filename)
                file_path = os.path.join(self.upload_folder, filename)
                
                # Save the uploaded file
                file.save(file_path)
                
                # Process based on file type
                if filename.lower().endswith('.ifc'):
                    # Convert IFC to JSON using the ingestor
                    json_filename = os.path.splitext(filename)[0] + '.json'
                    json_path = os.path.join(self.upload_folder, json_filename)
                    model_name = os.path.splitext(json_filename)[0]

                    if self.data_store_type == 'fileBased' and self.file_store.model_exists(model_name):
                        if not overwrite:
                            return jsonify({
                                'error': 'Model already exists',
                                'model_exists': True,
                                'model': model_name
                            }), 409
                        self.file_store.delete_model(model_name)
                    
                    converter = IFC2JSONSimple(file_path)
                    json_objects = converter.spf2Json()
                    
                    # Save JSON temporarily
                    with open(json_path, 'w') as f:
                        json.dump(json_objects, f, indent=2, default=str)
                    
                    # Store in data store
                    result = self.file_store.store(json_filename, json_objects)
                    
                    # Refresh memory tree with new data
                    self._refresh_memory_tree()
                    
                    # Clean up uploads
                    os.remove(file_path)
                    os.remove(json_path)

                    # Commit and push to git; get version ID
                    version_id = None
                    if self.git_manager:
                        version_id = self.git_manager.commit_and_push(model_name)
                    
                    return jsonify({
                        'filename': json_filename,
                        'entities_count': len(json_objects),
                        'stored_count': result.get('count', 0),
                        'store_path': result.get('path', ''),
                        'versionId': version_id,
                        'message': f"Successfully processed {len(json_objects)} entities"
                    })
                
                elif filename.lower().endswith('.json'):
                    # Load JSON and store
                    with open(file_path, 'r') as f:
                        json_objects = json.load(f)
                    
                    if not isinstance(json_objects, list):
                        return jsonify({'error': 'JSON file must contain an array of components'}), 400

                    model_name = os.path.splitext(filename)[0]
                    if self.data_store_type == 'fileBased' and self.file_store.model_exists(model_name):
                        if not overwrite:
                            return jsonify({
                                'error': 'Model already exists',
                                'model_exists': True,
                                'model': model_name
                            }), 409
                        self.file_store.delete_model(model_name)
                    
                    # Store in data store
                    result = self.file_store.store(filename, json_objects)
                    
                    # Refresh memory tree with new data
                    self._refresh_memory_tree()

                    # Commit and push to git; get version ID
                    version_id = None
                    if self.git_manager:
                        version_id = self.git_manager.commit_and_push(model_name)
                    
                    # Clean up upload
                    os.remove(file_path)
                    
                    return jsonify({
                        'filename': filename,
                        'entities_count': len(json_objects),
                        'stored_count': result.get('count', 0),
                        'store_path': result.get('path', ''),
                        'versionId': version_id,
                        'message': f"Successfully stored {len(json_objects)} entities"
                    })
                
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/status', methods=['GET'])
        def status():
            """Get server status"""
            latest_version = None
            if self.git_manager:
                latest_version = self.git_manager.get_latest_sha()
            preindex_status = (
                self.preindex_service.get_status(limit=10)
                if self.preindex_service
                else {
                    "enabled": False,
                    "trackedCount": 0,
                    "runningCount": 0,
                    "artifacts": {
                        "backend": "unconfigured",
                        "durable": False,
                        "counts": {"ready": 0, "pending": 0, "failed": 0, "missing": 0},
                        "latestReadyVersion": None,
                        "totalEntries": 0,
                    },
                    "entries": [],
                }
            )
            return jsonify({
                'status': 'running',
                'data_store': self.data_store_type,
                'timestamp': datetime.now().isoformat(),
                'version': '0.1.0',
                'latestVersion': latest_version,
                'latestIndexedVersion': (
                    preindex_status.get("artifacts", {}).get("latestReadyVersion")
                ),
                'storageMode': 'ephemeral' if os.getenv('VERCEL') else 'local',
                'indexedStorageBackend': preindex_status.get("artifacts", {}).get("backend"),
                'indexedStorageDurable': bool(preindex_status.get("artifacts", {}).get("durable")),
                'modelManagementMode': 'internal-only' if os.getenv('VERCEL') else 'local-admin',
                'indexingMode': 'demo-preindex' if preindex_status.get('enabled') else 'lazy-only',
                'preindex': preindex_status,
            })

        @self.app.route('/api/preindex/status', methods=['GET'])
        def preindex_status():
            """Return demo preindex readiness and tracked artifact state."""
            if not self.preindex_service:
                return jsonify({
                    'enabled': False,
                    'trackedCount': 0,
                    'runningCount': 0,
                    'artifacts': {
                        'backend': 'unconfigured',
                        'durable': False,
                        'counts': {'ready': 0, 'pending': 0, 'failed': 0, 'missing': 0},
                        'latestReadyVersion': None,
                        'totalEntries': 0,
                    },
                    'entries': [],
                })
            try:
                limit = int(request.args.get('limit', 25))
            except ValueError:
                limit = 25
            return jsonify(self.preindex_service.get_status(limit=max(1, limit)))

        @self.app.route('/api/preindex/trigger', methods=['POST'])
        def trigger_preindex():
            """Queue tracked files for best-effort demo preindexing."""
            guard_response = self._model_management_guard()
            if guard_response:
                return guard_response
            if not self.preindex_service:
                return jsonify({'error': 'Demo preindex is not configured'}), 501
            payload = request.get_json(silent=True) or {}
            force = bool(payload.get('force')) or request.args.get('force', '').lower() in (
                '1',
                'true',
                'yes',
            )
            result = self.preindex_service.enqueue_manifest(force=force)
            return jsonify(result)

        @self.app.route('/api/github/branches', methods=['GET'])
        def github_branches():
            """Proxy GitHub branch listing for the frontend."""
            try:
                repo_owner, repo_name, _, _ = self._parse_repo_query()
                per_page = int(request.args.get('perPage', 20))
                branches = proxy_list_branches(
                    repo_owner,
                    repo_name,
                    github_token=self._resolve_github_token(),
                    per_page=per_page,
                )
                return jsonify(branches)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except GitHubProxyError as exc:
                return jsonify({'error': str(exc)}), exc.status_code

        @self.app.route('/api/github/commits', methods=['GET'])
        def github_commits():
            """Proxy GitHub commit history for a branch or ref."""
            try:
                repo_owner, repo_name, ref_name, _ = self._parse_repo_query(require_ref=True)
                per_page = int(request.args.get('perPage', 35))
                commits = proxy_list_commits(
                    repo_owner,
                    repo_name,
                    ref_name,
                    github_token=self._resolve_github_token(),
                    per_page=per_page,
                )
                return jsonify(commits)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except GitHubProxyError as exc:
                return jsonify({'error': str(exc)}), exc.status_code

        @self.app.route('/api/github/file-history', methods=['GET'])
        def github_file_history():
            """Proxy GitHub commit history for one file path."""
            try:
                repo_owner, repo_name, ref_name, file_path = self._parse_repo_query(
                    require_ref=True,
                    require_path=True,
                )
                per_page = int(request.args.get('perPage', 20))
                commits = proxy_list_commits(
                    repo_owner,
                    repo_name,
                    ref_name,
                    github_token=self._resolve_github_token(),
                    per_page=per_page,
                    path=file_path,
                )
                return jsonify(commits)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except GitHubProxyError as exc:
                return jsonify({'error': str(exc)}), exc.status_code

        @self.app.route('/api/github/tree', methods=['GET'])
        def github_tree():
            """Proxy a recursive GitHub repository tree."""
            try:
                repo_owner, repo_name, ref_name, _ = self._parse_repo_query(require_ref=True)
                tree_entries = get_repo_tree(
                    repo_owner,
                    repo_name,
                    ref_name,
                    github_token=self._resolve_github_token(),
                )
                return jsonify(tree_entries)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except GitHubProxyError as exc:
                return jsonify({'error': str(exc)}), exc.status_code

        @self.app.route('/api/github/file', methods=['GET'])
        def github_file():
            """Stream a GitHub-hosted file through the backend."""
            try:
                repo_owner, repo_name, ref_name, file_path = self._parse_repo_query(
                    require_ref=True,
                    require_path=True,
                )
                file_bytes = fetch_file_bytes(
                    repo_owner,
                    repo_name,
                    ref_name,
                    file_path,
                    github_token=self._resolve_github_token(),
                )
                filename = os.path.basename(file_path) or 'file.bin'
                return Response(
                    file_bytes,
                    mimetype='application/octet-stream',
                    headers={
                        'Content-Disposition': f'inline; filename="{filename}"',
                        'Cache-Control': 'public, max-age=300',
                    },
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except GitHubFetchError as exc:
                return jsonify({'error': str(exc)}), exc.status_code
            except GitHubProxyError as exc:
                return jsonify({'error': str(exc)}), exc.status_code

        @self.app.route('/api/github/components', methods=['GET'])
        def github_components():
            """Query compact component metadata from a GitHub-hosted IFC file."""
            try:
                repo_owner, repo_name, ref_name, file_path = self._parse_repo_query(
                    require_ref=True,
                    require_path=True,
                )
                global_ids = self._split_csv_arg(request.args.get('guids', ''))
                if self.indexed_artifacts:
                    commit_sha = ref_name
                    if not self._looks_like_git_sha(ref_name):
                        commit_payloads = proxy_list_commits(
                            repo_owner,
                            repo_name,
                            ref_name,
                            github_token=self._resolve_github_token(),
                            per_page=1,
                            path=file_path,
                        )
                        if commit_payloads:
                            commit_sha = commit_payloads[0].get('sha') or ref_name
                        else:
                            commit_details = get_commit_details(
                                repo_owner,
                                repo_name,
                                ref_name,
                                github_token=self._resolve_github_token(),
                            )
                            commit_sha = commit_details.get('sha') or ref_name

                    preindexed_version_id = self.indexed_artifacts.build_version_id(
                        repo_owner,
                        repo_name,
                        commit_sha,
                        file_path,
                    )
                    summary = self.indexed_artifacts.read_summary(preindexed_version_id)
                    if summary is not None:
                        if global_ids:
                            selected_ids = set(global_ids)
                            summary = {
                                global_id: payload
                                for global_id, payload in summary.items()
                                if global_id in selected_ids
                            }
                        return jsonify(summary)
                file_bytes = fetch_file_bytes(
                    repo_owner,
                    repo_name,
                    ref_name,
                    file_path,
                    github_token=self._resolve_github_token(),
                )
                return jsonify(summarize_ifc_bytes(file_bytes, global_ids=global_ids))
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except GitHubFetchError as exc:
                return jsonify({'error': str(exc)}), exc.status_code
            except GitHubProxyError as exc:
                return jsonify({'error': str(exc)}), exc.status_code

        @self.app.route('/api/ifc/diff', methods=['POST'])
        def diff_ifc_models():
            """Compare two GitHub-hosted IFC revisions and return diff metadata."""
            payload = request.get_json(silent=True) or {}
            github_token = self._resolve_github_token(payload.get('githubToken'))

            def parse_model_ref(name):
                model_payload = payload.get(name) or {}
                github_url = str(model_payload.get('githubUrl', '')).strip()
                if github_url:
                    return parse_github_model_url(github_url)

                missing = [
                    field for field in ('repoOwner', 'repoName', 'commitSha', 'filePath')
                    if not str(model_payload.get(field, '')).strip()
                ]
                if missing:
                    raise ValueError(f"Missing {name} fields: {', '.join(missing)}")

                return GitHubModelRef(
                    repo_owner=model_payload['repoOwner'].strip(),
                    repo_name=model_payload['repoName'].strip(),
                    commit_sha=model_payload['commitSha'].strip(),
                    file_path=model_payload['filePath'].strip(),
                    github_url=github_url or None,
                )

            try:
                current_ref = parse_model_ref('current')
                last_ref = parse_model_ref('last')

                current_bytes = fetch_ifc_bytes(current_ref, github_token=github_token)
                try:
                    last_bytes = fetch_ifc_bytes(last_ref, github_token=github_token)
                except GitHubFileNotFoundError:
                    # If the IFC did not exist at the previous ref, treat the current model as entirely new.
                    last_bytes = None

                result = diff_ifc_bytes(current_bytes=current_bytes, last_bytes=last_bytes)
                result.update({
                    'compareSha': current_ref.commit_sha,
                    'baseSha': last_ref.commit_sha,
                    'current': {
                        'repoOwner': current_ref.repo_owner,
                        'repoName': current_ref.repo_name,
                        'commitSha': current_ref.commit_sha,
                        'filePath': current_ref.file_path,
                        'githubUrl': current_ref.github_url,
                    },
                    'last': {
                        'repoOwner': last_ref.repo_owner,
                        'repoName': last_ref.repo_name,
                        'commitSha': last_ref.commit_sha,
                        'filePath': last_ref.file_path,
                        'githubUrl': last_ref.github_url,
                    },
                })
                return jsonify(result)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except GitHubFetchError as exc:
                return jsonify({'error': str(exc)}), exc.status_code
            except Exception as exc:
                return jsonify({'error': str(exc)}), 500
        
        @self.app.route('/api/stores', methods=['GET'])
        def list_stores():
            """List available data stores"""
            return jsonify([
                {
                    'name': 'fileBased',
                    'description': 'File-based data store',
                    'status': 'active' if self.data_store_type == 'fileBased' else 'available'
                },
                {
                    'name': 'mongodbBased',
                    'description': 'MongoDB-based data store',
                    'status': 'active' if self.data_store_type == 'mongodbBased' else 'available'
                }
            ])
        
        @self.app.route('/api/entityGuids', methods=['GET'])
        def query_entity_guids():
            """Query for entity GUIDs
            
            Parameters:
            - models: comma-separated list of model names (optional)
            - entityTypes: comma-separated list of entity types (optional)
            - version: git commit SHA to query historical data (optional)
            
            Returns: Dictionary mapping model names to arrays of entity GUIDs
            """
            try:
                # Parse query parameters
                version = request.args.get('version')
                models = self._split_csv_arg(request.args.get('models', ''))
                entity_types = self._split_csv_arg(request.args.get('entityTypes', ''))

                tree = self._get_memory_tree(version)
                
                # If no specific models requested, use all available models
                if not models:
                    models = tree.get_models()

                expanded_types = self._expand_entity_types_for_models(entity_types, models, tree=tree) if entity_types else {}

                # Query and organize results by model
                result_by_model = {}
                for model_name in models:
                    model_entity_types = None
                    if entity_types:
                        model_entity_types = expanded_types.get(model_name, [])
                        if not model_entity_types:
                            continue

                    entity_guids = tree.get_entity_guids(
                        models=[model_name],
                        entity_types=model_entity_types
                    )
                    if entity_guids:
                        result_by_model[model_name] = entity_guids
                
                return jsonify(result_by_model)
            except Exception as e:
                return jsonify({'error': str(e)}), 400
        
        @self.app.route('/api/componentGuids', methods=['GET'])
        def query_component_guids():
            """Query for component GUIDs
            
            Parameters:
            - models: comma-separated list of model names (optional)
            - entityGuids: comma-separated list of entity GUIDs (optional)
            - entityTypes: comma-separated list of entity types (optional)
            - componentTypes: comma-separated list of component types (optional)
            - version: git commit SHA to query historical data (optional)
            
            Returns: Dictionary mapping model names to arrays of component GUIDs
            """
            try:
                # Parse query parameters
                version = request.args.get('version')
                models = self._split_csv_arg(request.args.get('models', ''))
                entity_guids = self._split_csv_arg(request.args.get('entityGuids', ''))
                entity_types = self._split_csv_arg(request.args.get('entityTypes', ''))
                component_types = self._split_csv_arg(request.args.get('componentTypes', ''))

                tree = self._get_memory_tree(version)
                
                # If no specific models requested, use all available models
                if not models:
                    models = tree.get_models()

                # Expand component types if provided
                if component_types:
                    expanded_comp_types = self._expand_component_types_for_models(component_types, models, tree=tree)
                    result_by_model = {}
                    for model_name in models:
                        model_comp_types = expanded_comp_types.get(model_name, [])
                        if model_comp_types:
                            component_guids = tree.get_component_guids_by_type(
                                component_types=model_comp_types,
                                models=[model_name]
                            )
                            if component_guids:
                                result_by_model[model_name] = component_guids
                    return jsonify(result_by_model)
                
                # Otherwise expand entity types
                expanded_types = self._expand_entity_types_for_models(entity_types, models, tree=tree) if entity_types else {}

                # Query and organize results by model
                result_by_model = {}
                for model_name in models:
                    model_entity_types = None
                    if entity_types:
                        model_entity_types = expanded_types.get(model_name, [])
                        if not model_entity_types:
                            continue

                    component_guids = tree.get_component_guids(
                        models=[model_name],
                        entity_guids=entity_guids,
                        entity_types=model_entity_types
                    )
                    if component_guids:
                        result_by_model[model_name] = component_guids
                
                return jsonify(result_by_model)
            except Exception as e:
                return jsonify({'error': str(e)}), 400
        
        @self.app.route('/api/components', methods=['GET'])
        def get_components():
            """Retrieve component data with flexible filtering, organized by model
            
            Parameters:
            - componentGuids: comma-separated list of specific component GUIDs (optional)
            - models: comma-separated list of model names (optional)
            - entityTypes: comma-separated list of entity types (optional)
            - entityGuids: comma-separated list of entity GUIDs (optional)
            - componentTypes: comma-separated list of component types (optional)
            - version: git commit SHA to query historical data (optional)
            
            Returns: Dictionary mapping model names to arrays of component objects
            """
            try:
                # Parse query parameters
                version = request.args.get('version')
                component_guids = self._split_csv_arg(request.args.get('componentGuids', ''))
                models = self._split_csv_arg(request.args.get('models', ''))
                entity_types = self._split_csv_arg(request.args.get('entityTypes', ''))
                entity_guids = self._split_csv_arg(request.args.get('entityGuids', ''))
                component_types = self._split_csv_arg(request.args.get('componentTypes', ''))

                tree = self._get_memory_tree(version)

                # If specific component GUIDs provided, use those directly
                if component_guids:
                    components, guid_to_model = tree.get_components(component_guids)
                # If component types provided, use those
                elif component_types:
                    search_models = models if models else tree.get_models()
                    expanded_comp_types = self._expand_component_types_for_models(component_types, search_models, tree=tree)
                    
                    found_guids = set()
                    for model_name in search_models:
                        model_comp_types = expanded_comp_types.get(model_name, [])
                        if model_comp_types:
                            model_guids = tree.get_component_guids_by_type(
                                component_types=model_comp_types,
                                models=[model_name]
                            )
                            found_guids.update(model_guids)
                    
                    components, guid_to_model = tree.get_components(list(found_guids), models=search_models)
                # Otherwise, use query filters to find components
                elif models or entity_types or entity_guids:
                    search_models = models if models else tree.get_models()
                    expanded_types = self._expand_entity_types_for_models(entity_types, search_models, tree=tree) if entity_types else {}

                    found_guids = set()
                    for model_name in search_models:
                        model_entity_types = None
                        if entity_types:
                            model_entity_types = expanded_types.get(model_name, [])
                            if not model_entity_types and not entity_guids:
                                continue
                        
                        model_guids = tree.get_component_guids(
                            models=[model_name],
                            entity_types=model_entity_types,
                            entity_guids=entity_guids
                        )
                        found_guids.update(model_guids)

                    # Get components, restricting search to the filtered models
                    components, guid_to_model = tree.get_components(list(found_guids), models=search_models)
                else:
                    # No filters specified - return all components from all models
                    all_guids = tree.get_component_guids()
                    components, guid_to_model = tree.get_components(all_guids)

                # Organize components by model using the guid_to_model mapping
                result_by_model = {}
                for component in components:
                    guid = component.get('componentGuid', '')
                    model_name = component.get('_model') or guid_to_model.get(guid, 'unknown')
                    component['_model'] = model_name
                    if model_name not in result_by_model:
                        result_by_model[model_name] = []
                    result_by_model[model_name].append(component)

                return jsonify(result_by_model)
            except Exception as e:
                return jsonify({'error': str(e)}), 400
        
        @self.app.route('/api/refresh', methods=['POST'])
        def refresh_memory():
            """Manually refresh the in-memory tree"""
            guard_response = self._model_management_guard()
            if guard_response:
                return guard_response
            try:
                count = self._refresh_memory_tree()
                return jsonify({
                    'models_loaded': count,
                    'message': f'Memory tree refreshed with {count} model(s)'
                })
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/models', methods=['GET'])
        def list_models():
            """List all loaded models
            
            Parameters:
            - version: git commit SHA to query historical data (optional)
            """
            guard_response = self._model_management_guard()
            if guard_response:
                return guard_response
            version = request.args.get('version')
            try:
                tree = self._get_memory_tree(version)
                models = tree.get_models()
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            return jsonify(models)

        @self.app.route('/api/models/details', methods=['GET'])
        def list_models_details():
            """List all stored models with metadata (file-based only)"""
            guard_response = self._model_management_guard()
            if guard_response:
                return guard_response
            if self.data_store_type != 'fileBased':
                return jsonify({'error': 'Model details are only available for fileBased store'}), 501

            return jsonify(self.file_store.list_directories())

        @self.app.route('/api/models/delete', methods=['POST'])
        def delete_models():
            """Delete one or more models and refresh the memory tree"""
            guard_response = self._model_management_guard()
            if guard_response:
                return guard_response
            if self.data_store_type != 'fileBased':
                return jsonify({'error': 'Delete is only available for fileBased store'}), 501

            payload = request.get_json(silent=True) or {}
            models = payload.get('models') or []
            if not models and payload.get('model'):
                models = [payload.get('model')]

            if not models:
                return jsonify({'error': 'No models provided'}), 400

            deleted = []
            missing = []
            for model_name in models:
                try:
                    if self.file_store.delete_model(model_name):
                        deleted.append(model_name)
                    else:
                        missing.append(model_name)
                except ValueError:
                    missing.append(model_name)

            if deleted:
                self._refresh_memory_tree()
                if self.git_manager:
                    self.git_manager.commit_deletion_and_push(deleted)

            return jsonify({
                'deleted': deleted,
                'missing': missing,
                'models_loaded': len(self.memory_tree.get_models())
            })
        
        @self.app.route('/api/entityTypes', methods=['GET'])
        def list_entity_types():
            """List all entity types in specified models
            
            Parameters:
            - models: comma-separated list of model names (optional)
            - version: git commit SHA to query historical data (optional)
            
            Returns: List of entity types
            """
            try:
                version = request.args.get('version')
                models = request.args.get('models', '')
                models = [m.strip() for m in models.split(',')] if models else None

                tree = self._get_memory_tree(version)
                types = tree.get_entity_types(models=models)
                
                return jsonify(types)
            except Exception as e:
                return jsonify({'error': str(e)}), 400
        
        @self.app.route('/api/componentTypes', methods=['GET'])
        def list_component_types():
            """List all component types in specified models
            
            Parameters:
            - models: comma-separated list of model names (optional)
            - version: git commit SHA to query historical data (optional)
            
            Returns: List of component types
            """
            try:
                version = request.args.get('version')
                models = request.args.get('models', '')
                models = [m.strip() for m in models.split(',')] if models else None

                tree = self._get_memory_tree(version)
                types = tree.get_component_types(models=models)
                
                return jsonify(types)
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/versions', methods=['GET'])
        def list_versions():
            """List recent versions (git commits that added/removed models).

            Parameters:
            - limit: maximum number of versions to return (default: 50)

            Returns: {latest: sha, versions: [{versionId, shortId, message, timestamp, author}]}
            """
            try:
                n = int(request.args.get('limit', 50))
            except ValueError:
                n = 50
            if self.preindex_service:
                versions = self.preindex_service.list_versions(limit=n)
                if versions:
                    latest = versions[0].get('versionId')
                    return jsonify({'latest': latest, 'versions': versions, 'source': 'preindex'})
            if self.git_manager is None:
                return jsonify({'error': 'Git versioning is not available'}), 501
            versions = self.git_manager.list_versions(n=n)
            latest = self.git_manager.get_latest_sha()
            return jsonify({'latest': latest, 'versions': versions, 'source': 'git'})

        @self.app.errorhandler(413)
        def too_large(e):
            """Handle file too large error"""
            return jsonify({'error': 'File is too large. Maximum size is 500MB'}), 413


def create_app(data_store_type='fileBased'):
    """Factory function to create and configure the Flask app
    
    Args:
        data_store_type: 'fileBased' or 'mongodbBased'
    
    Returns:
        Flask application instance
    """
    server = IFCProcessingServer(data_store_type=data_store_type)
    return server.app


if __name__ == '__main__':
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='IFC Processing Server with pluggable data store backends',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python server.py                    # Use default file-based backend
  python server.py --backend fileBased
  python server.py --backend mongodbBased
  python server.py -b fileBased --port 5001
  python server.py --help
        '''
    )
    
    parser.add_argument(
        '--backend', '-b',
        choices=['fileBased', 'mongodbBased'],
        default='fileBased',
        help='Data store backend to use (default: fileBased)'
    )
    
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0)'
    )
    
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=5001,
        help='Port to listen on (default: 5001)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable Flask debug mode'
    )
    
    args = parser.parse_args()
    
    # Validate backend choice
    if args.backend not in ['fileBased', 'mongodbBased']:
        print(f"❌ Unknown backend: {args.backend}")
        print("Available backends: fileBased, mongodbBased")
        sys.exit(1)
    
    # Create server
    server = IFCProcessingServer(data_store_type=args.backend)
    
    print("🚀 IFC Processing Server Starting...")
    print(f"💾 Data Store: {args.backend}")
    print(f"🌐 Host: {args.host}:{args.port}")
    print("📄 Admin Page: http://localhost:{}/".format(args.port) if args.host == '0.0.0.0' else f"http://{args.host}:{args.port}/")
    print("🔍 Viewer Page: http://localhost:{}/viewer".format(args.port) if args.host == '0.0.0.0' else f"http://{args.host}:{args.port}/viewer")
    print("\n📡 API Endpoints:")
    print("   POST   /api/upload                  - Upload & process IFC/JSON files")
    print("   POST   /api/ifc/diff                - Compare two IFC revisions")
    print("   GET    /api/entityGuids             - Query entity GUIDs")
    print("   GET    /api/componentGuids         - Query component GUIDs")
    print("   GET    /api/components              - Retrieve component data")
    print("   GET    /api/models                  - List all models")
    print("   GET    /api/entityTypes             - List entity types")
    print("   GET    /api/stores                  - List available data stores")
    print("   POST   /api/refresh                 - Manually refresh memory tree")
    print("   GET    /api/status                  - Server status")
    print("\n📁 Uploads: " + os.path.abspath(server.upload_folder))
    
    if args.backend == 'fileBased' and server.file_store:
        print("💾 File Store: " + os.path.abspath(server.file_store.base_path))
    
    print("\n" + "="*50)
    print("Press Ctrl+C to stop the server")
    print("="*50 + "\n")
    
    try:
        server.app.run(debug=args.debug, host=args.host, port=args.port)
    except KeyboardInterrupt:
        print("\n\n✅ Server stopped")
        sys.exit(0)

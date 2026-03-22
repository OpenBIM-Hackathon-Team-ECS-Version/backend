"""Core Flask server for IFC processing with pluggable data store backends"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

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
        UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
        ALLOWED_EXTENSIONS = {'ifc', 'json'}
        MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB max file size
        
        self.app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
        self.app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
        self.app.config['DATA_STORE_TYPE'] = self.data_store_type
        self.app.config['ALLOWED_EXTENSIONS'] = ALLOWED_EXTENSIONS
        
        # Ensure upload folder exists
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        # Store config for use in route handlers
        self.upload_folder = UPLOAD_FOLDER
        self.allowed_extensions = ALLOWED_EXTENSIONS
    
    def _initialize_backend(self):
        """Initialize the selected data store backend"""
        if self.data_store_type == 'fileBased':
            from fileBased import FileBasedStore
            from memoryTree import MemoryTree
            
            self.file_store = FileBasedStore()
            self.memory_tree = MemoryTree()
            
            # Refresh memory tree on startup
            self._refresh_memory_tree()
            print(f"[OK] Initialized file-based data store at: {self.file_store.base_path}")
            
        elif self.data_store_type == 'mongodbBased':
            from mongodbBased import MongoDBStore
            from mongodbMemoryTree import MongoDBMemoryTree
            
            self.file_store = MongoDBStore()
            self.memory_tree = MongoDBMemoryTree()
            
            print(f"✅ Initialized MongoDB data store")
        else:
            raise ValueError(f"Unknown data store type: {self.data_store_type}")
    
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

    def _expand_entity_types_for_models(self, entity_types, models):
        """Expand entity types to include all descendants, filtered by model."""
        if not entity_types:
            return {}

        print(f"[EXPAND] Input: entity_types={entity_types}, models={models}")
        
        search_models = models if models else self.memory_tree.get_models()
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
            model_types = set(self.memory_tree.get_entity_types(models=[model_name]))
            intersection = model_types.intersection(descendants)
            per_model[model_name] = sorted(list(intersection))
            print(f"[EXPAND] Model {model_name}: available={len(model_types)}, intersection={per_model[model_name]}")

        return per_model
    
    def _expand_component_types_for_models(self, component_types, models):
        """Expand component types to include all descendants, filtered by model.
        
        Component types are stored WITHOUT the "Component" suffix (e.g., IfcWall, IfcWallStandardCase).
        The user can query with or without "Component" suffix - both will work.
        """
        if not component_types:
            return {}

        print(f"\n🔍 _expand_component_types_for_models:")
        print(f"   Input component_types: {component_types}")

        search_models = models if models else self.memory_tree.get_models()
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
            model_types = set(self.memory_tree.get_component_types(models=[model_name]))
            intersection = model_types.intersection(descendants)
            per_model[model_name] = sorted(list(intersection))
            print(f"   Model '{model_name}': available types {len(model_types)}, intersection {len(intersection)}: {per_model[model_name]}")

        return per_model
    
    def _allowed_file(self, filename):
        """Check if file extension is allowed"""
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in self.app.config.get('ALLOWED_EXTENSIONS', [])
    
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
                    
                    return jsonify({
                        'filename': json_filename,
                        'entities_count': len(json_objects),
                        'stored_count': result.get('count', 0),
                        'store_path': result.get('path', ''),
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
                    
                    # Clean up upload
                    os.remove(file_path)
                    
                    return jsonify({
                        'filename': filename,
                        'entities_count': len(json_objects),
                        'stored_count': result.get('count', 0),
                        'store_path': result.get('path', ''),
                        'message': f"Successfully stored {len(json_objects)} entities"
                    })
                
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/status', methods=['GET'])
        def status():
            """Get server status"""
            return jsonify({
                'status': 'running',
                'data_store': self.data_store_type,
                'timestamp': datetime.now().isoformat(),
                'version': '0.1.0'
            })
        
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
            
            Returns: Dictionary mapping model names to arrays of entity GUIDs
            """
            try:
                # Parse query parameters
                models = request.args.get('models', '')
                entity_types = request.args.get('entityTypes', '')
                
                models = [m.strip() for m in models.split(',')] if models else None
                entity_types = [t.strip() for t in entity_types.split(',')] if entity_types else None
                
                # If no specific models requested, use all available models
                if not models:
                    models = self.memory_tree.get_models()

                expanded_types = self._expand_entity_types_for_models(entity_types, models) if entity_types else {}

                # Query and organize results by model
                result_by_model = {}
                for model_name in models:
                    model_entity_types = None
                    if entity_types:
                        model_entity_types = expanded_types.get(model_name, [])
                        if not model_entity_types and not entity_guids:
                            continue

                    entity_guids = self.memory_tree.get_entity_guids(
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
            
            Returns: Dictionary mapping model names to arrays of component GUIDs
            """
            try:
                # Parse query parameters
                models = request.args.get('models', '')
                entity_guids = request.args.get('entityGuids', '')
                entity_types = request.args.get('entityTypes', '')
                component_types = request.args.get('componentTypes', '')
                
                models = [m.strip() for m in models.split(',')] if models else None
                entity_guids = [e.strip() for e in entity_guids.split(',')] if entity_guids else None
                entity_types = [t.strip() for t in entity_types.split(',')] if entity_types else None
                component_types = [t.strip() for t in component_types.split(',')] if component_types else None
                
                # If no specific models requested, use all available models
                if not models:
                    models = self.memory_tree.get_models()

                # Expand component types if provided
                if component_types:
                    expanded_comp_types = self._expand_component_types_for_models(component_types, models)
                    result_by_model = {}
                    for model_name in models:
                        model_comp_types = expanded_comp_types.get(model_name, [])
                        if model_comp_types:
                            component_guids = self.memory_tree.get_component_guids_by_type(
                                component_types=model_comp_types,
                                models=[model_name]
                            )
                            if component_guids:
                                result_by_model[model_name] = component_guids
                    return jsonify(result_by_model)
                
                # Otherwise expand entity types
                expanded_types = self._expand_entity_types_for_models(entity_types, models) if entity_types else {}

                # Query and organize results by model
                result_by_model = {}
                for model_name in models:
                    model_entity_types = None
                    if entity_types:
                        model_entity_types = expanded_types.get(model_name, [])
                        if not model_entity_types:
                            continue

                    component_guids = self.memory_tree.get_component_guids(
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
            
            Returns: Dictionary mapping model names to arrays of component objects
            """
            try:
                with open('api_debug.log', 'a') as f:
                    f.write(f"\n[GET_COMPONENTS] New request\n")
                
                # Parse query parameters
                component_guids_param = request.args.get('componentGuids', '')
                models = request.args.get('models', '')
                entity_types = request.args.get('entityTypes', '')
                entity_guids = request.args.get('entityGuids', '')
                component_types = request.args.get('componentTypes', '')
                
                # Parse into lists
                component_guids = [g.strip() for g in component_guids_param.split(',')] if component_guids_param else None
                models = [m.strip() for m in models.split(',')] if models else None
                entity_types = [t.strip() for t in entity_types.split(',')] if entity_types else None
                entity_guids = [g.strip() for g in entity_guids.split(',')] if entity_guids else None
                component_types = [t.strip() for t in component_types.split(',')] if component_types else None
                
                with open('api_debug.log', 'a') as f:
                    f.write(f"  models={models}\n")
                    f.write(f"  entity_types={entity_types}\n")
                    f.write(f"  entity_guids={entity_guids}\n")
                    f.write(f"  component_types={component_types}\n")
                
                # If specific component GUIDs provided, use those directly
                if component_guids:
                    with open('api_debug.log', 'a') as f:
                        f.write(f"  -> Branch 1: component_guids\n")
                    components, guid_to_model = self.memory_tree.get_components(component_guids)
                # If component types provided, use those
                elif component_types:
                    with open('api_debug.log', 'a') as f:
                        f.write(f"  -> Branch 2: component_types\n")
                    search_models = models if models else self.memory_tree.get_models()
                    expanded_comp_types = self._expand_component_types_for_models(component_types, search_models)
                    
                    found_guids = set()
                    for model_name in search_models:
                        model_comp_types = expanded_comp_types.get(model_name, [])
                        if model_comp_types:
                            model_guids = self.memory_tree.get_component_guids_by_type(
                                component_types=model_comp_types,
                                models=[model_name]
                            )
                            found_guids.update(model_guids)
                    
                    components, guid_to_model = self.memory_tree.get_components(list(found_guids), models=search_models)
                # Otherwise, use query filters to find components
                elif models or entity_types or entity_guids:
                    with open('api_debug.log', 'a') as f:
                        f.write(f"  -> Branch 3: query filters (models OR entity_types OR entity_guids)\n")
                    search_models = models if models else self.memory_tree.get_models()
                    with open('api_debug.log', 'a') as f:
                        f.write(f"     search_models={search_models}\n")
                        f.write(f"     Calling _expand_entity_types_for_models({entity_types}, {search_models})\n")
                    expanded_types = self._expand_entity_types_for_models(entity_types, search_models) if entity_types else {}
                    with open('api_debug.log', 'a') as f:
                        f.write(f"     expanded_types={expanded_types}\n")

                    found_guids = set()
                    for model_name in search_models:
                        model_entity_types = None
                        if entity_types:
                            model_entity_types = expanded_types.get(model_name, [])
                            if not model_entity_types and not entity_guids:
                                continue

                        with open('api_debug.log', 'a') as f:
                            f.write(f"     Model {model_name}: calling get_component_guids with entity_types={model_entity_types}\n")
                        
                        model_guids = self.memory_tree.get_component_guids(
                            models=[model_name],
                            entity_types=model_entity_types,
                            entity_guids=entity_guids
                        )
                        with open('api_debug.log', 'a') as f:
                            f.write(f"     Model {model_name}: found {len(model_guids)} guids\n")
                        found_guids.update(model_guids)

                    # Get components, restricting search to the filtered models
                    components, guid_to_model = self.memory_tree.get_components(list(found_guids), models=search_models)
                else:
                    # No filters specified - return all components from all models
                    all_guids = self.memory_tree.get_component_guids()
                    components, guid_to_model = self.memory_tree.get_components(all_guids)
                
                with open('api_debug.log', 'a') as f:
                    f.write(f"  Found {len(components)} total components\n")
                
                # Organize components by model using the guid_to_model mapping
                result_by_model = {}
                for component in components:
                    guid = component.get('componentGuid', '')
                    model_name = component.get('_model') or guid_to_model.get(guid, 'unknown')
                    component['_model'] = model_name
                    if model_name not in result_by_model:
                        result_by_model[model_name] = []
                    result_by_model[model_name].append(component)
                
                with open('api_debug.log', 'a') as f:
                    f.write(f"  Returning {len(result_by_model)} models\n")
                
                return jsonify(result_by_model)
            except Exception as e:
                return jsonify({'error': str(e)}), 400
        
        @self.app.route('/api/refresh', methods=['POST'])
        def refresh_memory():
            """Manually refresh the in-memory tree"""
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
            """List all loaded models"""
            models = self.memory_tree.get_models()
            return jsonify(models)

        @self.app.route('/api/models/details', methods=['GET'])
        def list_models_details():
            """List all stored models with metadata (file-based only)"""
            if self.data_store_type != 'fileBased':
                return jsonify({'error': 'Model details are only available for fileBased store'}), 501

            return jsonify(self.file_store.list_directories())

        @self.app.route('/api/models/delete', methods=['POST'])
        def delete_models():
            """Delete one or more models and refresh the memory tree"""
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
            
            Returns: List of entity types
            """
            try:
                models = request.args.get('models', '')
                models = [m.strip() for m in models.split(',')] if models else None
                
                types = self.memory_tree.get_entity_types(models=models)
                
                return jsonify(types)
            except Exception as e:
                return jsonify({'error': str(e)}), 400
        
        @self.app.route('/api/componentTypes', methods=['GET'])
        def list_component_types():
            """List all component types in specified models
            
            Parameters:
            - models: comma-separated list of model names (optional)
            
            Returns: List of component types
            """
            try:
                models = request.args.get('models', '')
                models = [m.strip() for m in models.split(',')] if models else None
                
                types = self.memory_tree.get_component_types(models=models)
                
                return jsonify(types)
            except Exception as e:
                return jsonify({'error': str(e)}), 400
        
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
        default=5000,
        help='Port to listen on (default: 5000)'
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

"""File-based data store for IFC components"""

import os
import json
import shutil
from pathlib import Path

class FileBasedStore:
    """Store components in a file-based directory structure"""
    
    def __init__(self, base_path=None):
        """Initialize the file-based store
        
        Args:
            base_path: Base directory for the data store. Defaults to 'dataStores/fileBased/data'
        """
        if base_path is None:
            base_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'dataStores',
                'fileBased',
                'data'
            )
        
        self.base_path = base_path
        os.makedirs(self.base_path, exist_ok=True)
    
    def store(self, filename, components):
        """Store components from a file
        
        Args:
            filename: Name of the source file (used to create directory)
            components: List of component dictionaries
            
        Returns:
            Dictionary with store result information
        """
        # Create directory name from filename (remove extension)
        dir_name = os.path.splitext(filename)[0]
        dir_path = os.path.join(self.base_path, dir_name)
        
        # Create the directory
        os.makedirs(dir_path, exist_ok=True)
        
        # Store each component as a separate file
        stored_count = 0
        
        for component in components:
            # Get entityGuid and guid from component
            entity_guid = component.get('entityGuid', 'unknown')
            componentGuid = component.get('componentGuid', 'unknown')
            
            # Create filename: entityGuid_guid.json
            component_filename = f"{entity_guid}_{componentGuid}.json"
            component_path = os.path.join(dir_path, component_filename)
            
            # Write component to file
            try:
                with open(component_path, 'w') as f:
                    json.dump(component, f, indent=2, default=str)
                stored_count += 1
            except Exception as e:
                print(f"Error storing component {component_filename}: {e}")
        
        return {
            'success': True,
            'count': stored_count,
            'path': dir_path,
            'directory': dir_name
        }
    
    def retrieve(self, directory):
        """Retrieve all components from a directory
        
        Args:
            directory: Directory name to retrieve from
            
        Returns:
            List of component dictionaries
        """
        dir_path = os.path.join(self.base_path, directory)
        
        if not os.path.isdir(dir_path):
            return []
        
        components = []
        
        for filename in os.listdir(dir_path):
            if filename.endswith('.json'):
                file_path = os.path.join(dir_path, filename)
                try:
                    with open(file_path, 'r') as f:
                        component = json.load(f)
                        components.append(component)
                except Exception as e:
                    print(f"Error reading component {filename}: {e}")
        
        return components
    
    def list_directories(self):
        """List all stored directories
        
        Returns:
            List of directory names
        """
        if not os.path.isdir(self.base_path):
            return []
        
        directories = []
        for item in os.listdir(self.base_path):
            item_path = os.path.join(self.base_path, item)
            if os.path.isdir(item_path):
                # Count JSON files in directory
                json_files = [f for f in os.listdir(item_path) if f.endswith('.json')]
                directories.append({
                    'name': item,
                    'component_count': len(json_files)
                })
        
        return directories

    def model_exists(self, model_name):
        """Check if a model directory exists."""
        if not model_name or model_name in ('.', '..'):
            return False
        if os.path.sep in model_name or (os.path.altsep and os.path.altsep in model_name):
            return False
        dir_path = os.path.join(self.base_path, model_name)
        return os.path.isdir(dir_path)

    def delete_model(self, model_name):
        """Delete a model directory and all contained files.

        Returns:
            True if deleted, False if not found
        """
        if not model_name or model_name in ('.', '..'):
            return False
        if os.path.sep in model_name or (os.path.altsep and os.path.altsep in model_name):
            return False

        # Prevent path traversal by resolving inside base_path
        base_path = Path(self.base_path).resolve()
        target_path = (base_path / model_name).resolve()

        if base_path not in target_path.parents:
            raise ValueError("Invalid model path")

        if not target_path.is_dir():
            return False

        shutil.rmtree(target_path)
        return True

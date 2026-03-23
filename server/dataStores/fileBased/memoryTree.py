"""In-memory tree structure for component storage and querying (fileBased)"""

import os
import gzip
import json
from pathlib import Path
from typing import Dict, List, Optional, Set

class MemoryTree:
    """In-memory tree structure for fast component querying"""
    
    def __init__(self):
        """Initialize the memory tree"""
        self.models: Dict = {}  # models[model_name] = {by_entity, by_type, by_entityType, by_componentGuid}
        self.by_entity: Dict[str, List[Dict]] = {}
        self.by_componentGuid: Dict[str, List[Dict]] = {}

    def _reset_indexes(self):
        self.models = {}
        self.by_entity = {}
        self.by_componentGuid = {}

    def _ensure_model(self, model_name: str):
        if model_name not in self.models:
            self.models[model_name] = {
                'by_entity': {},
                'by_type': {},
                'by_entityType': {},
                'entity_types': {},
                'by_componentGuid': {}
            }
        return self.models[model_name]

    def _index_component(self, model_name: str, component: Dict):
        model = self._ensure_model(model_name)

        component_guid = component.get('componentGuid')
        if not component_guid:
            return

        model['by_componentGuid'][component_guid] = component

        if component_guid not in self.by_componentGuid:
            self.by_componentGuid[component_guid] = []
        self.by_componentGuid[component_guid].append({
            'model': model_name,
            'component': component
        })

        entity_guid = component.get('entityGuid')
        if entity_guid:
            if entity_guid not in model['by_entity']:
                model['by_entity'][entity_guid] = []
            model['by_entity'][entity_guid].append(component_guid)

            if entity_guid not in self.by_entity:
                self.by_entity[entity_guid] = []
            self.by_entity[entity_guid].append({
                'model': model_name,
                'componentGuid': component_guid
            })

            entity_type = component.get('entityType')
            if entity_type:
                if entity_guid in model['entity_types']:
                    existing_type = model['entity_types'][entity_guid]
                    if existing_type != entity_type:
                        print(f"⚠️  WARNING: Entity {entity_guid} has conflicting types: '{existing_type}' vs '{entity_type}'")
                        print(f"   Component 1: {model['by_entity'][entity_guid][0]}")
                        print(f"   Component 2: {component_guid}")
                else:
                    model['entity_types'][entity_guid] = entity_type
                    if entity_type not in model['by_entityType']:
                        model['by_entityType'][entity_type] = []
                    model['by_entityType'][entity_type].append(entity_guid)

        component_type = component.get('componentType', 'Unknown')
        if component_type.endswith('Component'):
            component_type = component_type[:-9]

        if component_type not in model['by_type']:
            model['by_type'][component_type] = []
        model['by_type'][component_type].append(component_guid)

    
    def refresh_from_store(self, store_path: str):
        """Refresh memory tree from file-based store
        
        Args:
            store_path: Path to the file-based data store
        """
        self._reset_indexes()
        
        if not os.path.isdir(store_path):
            return
        
        # Iterate through each model directory
        for model_name in os.listdir(store_path):
            model_path = os.path.join(store_path, model_name)
            
            if not os.path.isdir(model_path):
                continue
            
            self._ensure_model(model_name)
            
            # Load all components for this model
            for filename in os.listdir(model_path):
                if not filename.endswith('.json'):
                    continue
                
                component_path = os.path.join(model_path, filename)
                try:
                    with open(component_path, 'r') as f:
                        component = json.load(f)
                    
                    self._index_component(model_name, component)
                    
                except Exception as e:
                    print(f"Error loading component {filename}: {e}")

    def refresh_from_components(self, model_name: str, components: List[Dict]):
        """Refresh indexes from a list of in-memory components for one model."""
        self._reset_indexes()
        self._ensure_model(model_name)
        for component in components or []:
            if isinstance(component, dict):
                self._index_component(model_name, component)

    def refresh_from_snapshot_payload(self, payload: Dict):
        """Refresh indexes from an already-loaded snapshot payload."""
        self._reset_indexes()
        models = payload.get('models') if isinstance(payload, dict) else None
        if not isinstance(models, dict):
            return

        for model_name, components in models.items():
            if not isinstance(model_name, str):
                continue
            self._ensure_model(model_name)
            if not isinstance(components, list):
                continue
            for component in components:
                if isinstance(component, dict):
                    self._index_component(model_name, component)

    def refresh_from_snapshot(self, snapshot_path: str):
        """Refresh indexes from a packed JSON or JSON.GZ snapshot."""
        if not snapshot_path or not os.path.isfile(snapshot_path):
            self._reset_indexes()
            return

        opener = gzip.open if snapshot_path.endswith('.gz') else open
        with opener(snapshot_path, 'rt', encoding='utf-8') as handle:
            payload = json.load(handle)

        self.refresh_from_snapshot_payload(payload)
    
    def get_entity_guids(self, 
                        models: Optional[List[str]] = None,
                        entity_types: Optional[List[str]] = None,
                        components: Optional[List[str]] = None) -> List[str]:
        """Query for entity GUIDs
        
        Args:
            models: List of model names (None = all models)
            entity_types: List of entity types to filter by (None = all types)
            components: List of component GUIDs to filter by (None = all components)
            
        Returns:
            List of entity GUIDs matching the criteria
        """
        # Determine which models to search
        search_models = models if models else list(self.models.keys())
        
        entity_guids: Set[str] = set()
        
        for model_name in search_models:
            if model_name not in self.models:
                continue
            
            model = self.models[model_name]
            model_entities: Set[str] = set()
            
            # If entity_types specified, get entities of those types
            if entity_types:
                for entity_type in entity_types:
                    if entity_type in model['by_entityType']:
                        model_entities.update(model['by_entityType'][entity_type])
            else:
                # Get all entity GUIDs in this model
                model_entities.update(model['by_entity'].keys())
            
            # If components specified, filter to only those components' entities
            if components:
                component_entities: Set[str] = set()
                for component_guid in components:
                    if component_guid in model['by_componentGuid']:
                        entity_guid = model['by_componentGuid'][component_guid].get('entityGuid')
                        if entity_guid:
                            component_entities.add(entity_guid)
                model_entities.intersection_update(component_entities)
            
            # Union with result from other models
            entity_guids.update(model_entities)
        
        return sorted(list(entity_guids))
    
    def get_component_guids(self,
                           models: Optional[List[str]] = None,
                           entity_guids: Optional[List[str]] = None,
                           entity_types: Optional[List[str]] = None) -> List[str]:
        """Query for component GUIDs
        
        Args:
            models: List of model names (None = all models)
            entity_guids: List of entity GUIDs to filter by (None = all entities)
            entity_types: List of entity types to filter by (None = all types)
            
        Returns:
            List of component GUIDs matching the criteria
        """
        # Determine which models to search
        search_models = models if models else list(self.models.keys())
        print(f"\n🔍 get_component_guids ENTRY:")
        print(f"   Input models param: {models}")
        print(f"   Determined search_models: {search_models}")
        print(f"   Available models: {list(self.models.keys())}")
        print(f"   entity_types: {entity_types}")
        print(f"   entity_guids: {entity_guids}")
        
        result_guids: Set[str] = None
        
        for model_name in search_models:
            if model_name not in self.models:
                print(f"  ⚠️  Model '{model_name}' not found in available models: {list(self.models.keys())}")
                continue
            
            print(f"\n  Processing model: {model_name}")
            model = self.models[model_name]
            model_guids: Set[str] = set()
            filter_entity_guids: Set[str] = set()
            
            # If entity_types specified, get entity GUIDs for those types
            if entity_types:
                for entity_type in entity_types:
                    if entity_type in model['by_entityType']:
                        filter_entity_guids.update(model['by_entityType'][entity_type])
                print(f"    Found {len(filter_entity_guids)} entities with matching types")
            
            # If entity_guids specified, add them to the filter
            if entity_guids:
                filter_entity_guids.update(entity_guids)
                print(f"    Filter entities now: {len(filter_entity_guids)}")
            
            # Get components for the filtered entities
            if filter_entity_guids:
                for entity_guid in filter_entity_guids:
                    if entity_guid in model['by_entity']:
                        model_guids.update(model['by_entity'][entity_guid])
                print(f"    Found {len(model_guids)} components for filtered entities")
            else:
                # No entity-level filters, get all components
                model_guids = set(model['by_componentGuid'].keys())
                print(f"    No entity filters - getting all {len(model_guids)} components from this model")
            
            # Union with result from other models
            if result_guids is None:
                result_guids = model_guids
                print(f"    First model - initialized result_guids with {len(result_guids)} components")
            else:
                before = len(result_guids)
                result_guids.update(model_guids)
                print(f"    Added {len(model_guids)} components (total now: {len(result_guids)}, added: {len(result_guids) - before})")
        
        final_count = len(result_guids or set())
        print(f"\n✓ get_component_guids EXIT: Returning {final_count} total components")
        return sorted(list(result_guids or set()))
    
    def get_components(self, guids: List[str], models: Optional[List[str]] = None):
        """Retrieve component data by GUIDs
        
        Args:
            guids: List of component GUIDs to retrieve
            models: List of model names to search (None = all models)
            
        Returns:
            Tuple of (components_list, guid_to_model_dict)
            - components_list: List of component dictionaries (without model field)
            - guid_to_model_dict: Dict mapping each component GUID to its model name
        """
        components = []
        guid_to_model = {}
        
        # Determine which models to search
        search_models = models if models else list(self.models.keys())
        
        # Search only the specified models for the GUIDs
        for model_name in search_models:
            if model_name not in self.models:
                continue
                
            model = self.models[model_name]
            
            for guid in guids:
                if guid in model['by_componentGuid']:
                    component = model['by_componentGuid'][guid].copy()
                    component['_model'] = model_name
                    components.append(component)
                    if guid not in guid_to_model:
                        guid_to_model[guid] = model_name
        
        return components, guid_to_model

    def get_flat_entities(self):
        """Return flattened entity index across all models.

        Returns:
            Dict mapping entityGuid -> List[Dict(model, componentGuid)]
        """
        return {entity_guid: entries.copy() for entity_guid, entries in self.by_entity.items()}
    
    def get_models(self) -> List[str]:
        """Get list of all loaded models
        
        Returns:
            List of model names
        """
        return sorted(list(self.models.keys()))
    
    def get_entity_types(self, models: Optional[List[str]] = None) -> List[str]:
        """Get list of all entity types across models
        
        Args:
            models: List of model names (None = all models)
            
        Returns:
            List of entity types
        """
        search_models = models if models else list(self.models.keys())
        types: Set[str] = set()
        
        for model_name in search_models:
            if model_name in self.models:
                types.update(self.models[model_name]['by_entityType'].keys())
        
        return sorted(list(types))
    
    def get_component_types(self, models: Optional[List[str]] = None) -> List[str]:
        """Get list of all component types across models
        
        Args:
            models: List of model names (None = all models)
            
        Returns:
            List of component types
        """
        search_models = models if models else list(self.models.keys())
        types: Set[str] = set()
        
        for model_name in search_models:
            if model_name in self.models:
                types.update(self.models[model_name]['by_type'].keys())
        
        return sorted(list(types))
    
    def get_component_guids_by_type(self, 
                                    component_types: Optional[List[str]] = None,
                                    models: Optional[List[str]] = None) -> List[str]:
        """Query for component GUIDs by component type
        
        Args:
            component_types: List of component types to filter by (None = all types)
            models: List of model names (None = all models)
            
        Returns:
            List of component GUIDs matching the criteria
        """
        # Determine which models to search
        search_models = models if models else list(self.models.keys())
        
        result_guids: Set[str] = set()
        
        for model_name in search_models:
            if model_name not in self.models:
                continue
            
            model = self.models[model_name]
            model_guids: Set[str] = set()
            
            # If component_types specified, get components of those types
            if component_types:
                for comp_type in component_types:
                    if comp_type in model['by_type']:
                        model_guids.update(model['by_type'][comp_type])
            else:
                # No type filters, get all components
                model_guids = set(model['by_componentGuid'].keys())
            
            # Union with result from other models
            result_guids.update(model_guids)
        
        return sorted(list(result_guids))

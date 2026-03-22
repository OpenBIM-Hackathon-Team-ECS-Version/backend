  # IFCJSON_python - ifc2json_simple.py
# Simplified IFC to JSON converter - prints first-level attributes
# https://github.com/IFCJSON-Team

# MIT License

from datetime import datetime
import hashlib
import uuid
import json
import sys
import argparse

try:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.guid as guid
except ImportError as e:
    print(f"Warning: ifcopenshell not available: {e}")
    ifcopenshell = None
    guid = None

from utils import toLowerCamelcase, generateDeterministicGuid, expandGuid

# include empty properties in output (as empty strings) 
INCLUDE_EMPTY_PROPERTIES = False

#only these top level nodes are exported, all are imported
ALLOWED_TYPES = {
    'IfcObjectDefinition', 
    'IfcPropertySet', 
    'IfcRelationship'
    }

# Define attributes to exclude
EXCLUDE_ATTRIBUTES = {
    'ownerhistory',
    'id',
    'step_id',
    'objectplacement',
    'representation',
    'representations',
    'representationmaps',
    'representationcontexts',
    'unitsincontext',
    'globalId'
}

# Define attribute name substitutions
ATTRIBUTE_SUBSTITUTIONS = {
    'Name': 'componentName',
    'Description': 'componentDescription',
    'HasPropertySets': 'propertySets',
    'type': 'componentType'
}

EXPAND_GUID_ATTRIBUTES = {'GlobalId'}

class IFC2JSONSimple:
    """Simplified IFC to JSON converter that prints entity attributes"""
    
    SCHEMA_VERSION = '0.0.1'

    settings = None  # Lazy-loaded in __init__
    
    def __init__(self, ifcModel, EMPTY_PROPERTIES=False, modelName=None):
        """IFC SPF simplified converter

        parameters:
        ifcModel: IFC filePath or ifcopenshell model instance
        EMPTY_PROPERTIES (boolean): if True then empty properties are included
        modelName (str): Optional model name for deterministic GUID generation
        """
        if ifcopenshell is None:
            raise RuntimeError("ifcopenshell is not installed. Cannot process IFC files.")
        
        # Lazy-load settings on first initialization
        if IFC2JSONSimple.settings is None:
            IFC2JSONSimple.settings = ifcopenshell.geom.settings()
            IFC2JSONSimple.settings.set("use-world-coords", True)
        
        if isinstance(ifcModel, ifcopenshell.file):
            self.ifcModel = ifcModel
        else:
            self.ifcModel = ifcopenshell.open(ifcModel)
        self.EMPTY_PROPERTIES = EMPTY_PROPERTIES
        self.modelName = modelName or "unknown"

        # Dictionary referencing all objects with a GlobalId that are already created
        self.rootObjects = {}

        # Dictionary referencing all objects with a GlobalId that are already created
        self.includeObjects = {}

        # Representations are kept seperate to be added to the end of the list
        self.representations = []



    # def createReferenceObject(self, currentAttributes, COMPACT=False):
    #     """Returns object reference

    #     Parameters:
    #     currentAttributes (dict): Dictionary of IFC object data
    #     COMPACT (boolean): verbose or non verbose IFC.JSON-5a output

    #     Returns:
    #     dict: object containing reference to another object

    #     """
    #     ref = {}
    #     if not COMPACT:

    #         # Entity names must be stripped of Ifc prefix
    #         ref['type'] = currentAttributes['type'][3:]
    #     ref['ref'] = expandGuid(currentAttributes['GlobalId']) if 'GlobalId' in currentAttributes else None
    #     return ref

    def spf2Json(self):
        """
        Iterate through all entities in the IFC file and print their first-level attributes

        Returns:
        list: List of dictionaries containing entity attributes
        """        


        jsonObjects = []

        for entity in self.ifcModel:
            if hasattr(entity, 'GlobalId') and entity.GlobalId:
                self.rootObjects[entity.id()] = guid.split(
                    guid.expand(entity.GlobalId))[1:-1]
        
        # Create a list of entities by querying for each allowed type
        entity_list = []
        # for allowed_type in reversed(list(ALLOWED_TYPES)):
        for allowed_type in ALLOWED_TYPES:
            entities_of_type = self.ifcModel.by_type(allowed_type)
            entity_list.extend(entities_of_type)
        
        # Iterate through all queried entities

        for entity in entity_list:
            returnedValue = self.processEntry(entity, topLevel=True)
            if returnedValue is not None:   
                jsonObjects.append(returnedValue)

        jsonObjects = jsonObjects + list(self.representations)
        return jsonObjects
    
    def processEntry(self, entity, topLevel=False):
        # Get entity type
        if not topLevel and hasattr(entity, 'GlobalId'):
            entityGuid = expandGuid(entity.GlobalId)
            return entityGuid
        
        entity_type = entity.is_a()
        
        entity_dict = {}
        

        
        # Get all first-level attributes from __dict__
        entityAttributes = entity.__dict__
        
        # Convert all attribute keys to toLowerCamelcase
        entityAttributes = {toLowerCamelcase(key): value for key, value in entityAttributes.items()}


        if(entity.is_a('IfcObjectDefinition')):
            entityAttributes['entityType'] = entity_type
            entityAttributes['entityGuid'] = expandGuid(entity.GlobalId) 
            entityAttributes['componentGuid'] = generateDeterministicGuid(self.modelName, entity_type, entityAttributes['entityGuid']) 
            entityAttributes.pop('globalId', None)

        if(entity.is_a('IfcRelationship')):
            # entityAttributes['entityGuid'] = ""
            entityAttributes['componentGuid'] = expandGuid(entityAttributes['globalId']) 
            entityAttributes.pop('globalId', None)
            
            # Find the first key starting with 'Relating' and use its value as entityGuid
            relating_keys = sorted([key for key in entityAttributes.keys() if key.startswith('relating')])
            if relating_keys:
                first_relating_key = relating_keys[0]
                relating_value = entityAttributes[first_relating_key]
                # Extract entityGuid from the relating entity
                if isinstance(relating_value, str):
                    entityAttributes['entityGuid'] = expandGuid(relating_value)
                    #entityAttributes.pop(first_relating_key, None)
                elif hasattr(relating_value, 'GlobalId'): # not sure about this case.
                    entityAttributes['entityGuid'] = expandGuid(relating_value.GlobalId)
                    #entityAttributes.pop(first_relating_key, None)
                else:
                   entityAttributes['entityGuid'] = ""




        if(entity.is_a('IfcPropertySet')):
            entityAttributes['componentGuid'] = expandGuid(entity.GlobalId) 
            entityAttributes.pop('globalId', None)
            if hasattr(entity, 'PropertyDefinitionOf') and len(entity.PropertyDefinitionOf) > 0:
                relation = entity.PropertyDefinitionOf[0]
                testentity = relation.RelatedObjects[0]
                if hasattr(testentity, 'GlobalId'):
                    entityAttributes['entityGuid'] = expandGuid(testentity.GlobalId)
            else:
                print(f"Warning: IfcPropertySet {entityAttributes['componentGuid']} of type {entity_type} has no related objects with GlobalId")

        if 'representation' in entityAttributes:
                obj = self.toObj(entity)

                if obj:
                    entityGuid = expandGuid(entity.GlobalId)
                    componentGuid = generateDeterministicGuid(self.modelName, "ShapeRepresentationComponent", entityGuid)    
                    self.representations.append(
                        {
                            "componentGuid": componentGuid,
                            "componentType": "IfcShapeRepresentationComponent",
                            "entityGuid": entityGuid,
                            "representationIdentifier": "Body",
                            "representationFormat": "OBJ",
                            "items": [
                                obj
                            ]
                        }
                    )

        returnedAttributes = self.appendAttributes(entityAttributes, entity_type)
        entity_dict.update(returnedAttributes)
        
        # Sort entity_dict alphabetically by keys
        entity_dict = dict(sorted(entity_dict.items()))

        return entity_dict

    def appendAttributes(self, currentAttributes, entity_type):

        entity_dict = {}        
        
        keys = sorted(currentAttributes.keys())  
            
        for attr_name in keys:
            # Skip excluded attributes
            if attr_name.lower() in EXCLUDE_ATTRIBUTES:
                continue

            # Skip internal attributes
            if attr_name.startswith('_'):
                continue
            
            attr_value = currentAttributes[attr_name]
                            
            # Convert to JSON-serializable format using getAttributeValue
            try:
                json_value = self.getAttributeValueNew(attr_value)
            except:
                json_value = None
            
            # If this is GlobalId (converted to entityGuid), expand it to standard UUID format
            if attr_name in EXPAND_GUID_ATTRIBUTES and json_value is not None:
                json_value = expandGuid(json_value)

            
            # If this is the type attribute being converted to componentType, append "Component"
            if attr_name == 'type' and json_value is not None:
                json_value = json_value + 'Component'
            
            # Print attribute name and type
            attr_type = type(attr_value).__name__

            # Apply substitution to attribute name if it exists in ATTRIBUTE_SUBSTITUTIONS
            if attr_name in ATTRIBUTE_SUBSTITUTIONS:
                display_attr_name = ATTRIBUTE_SUBSTITUTIONS[attr_name]
            else:
                display_attr_name = attr_name
                            
            # Convert to camelCase
            display_attr_name = toLowerCamelcase(display_attr_name)
            
            # Only add to dict if there's a value or if empty properties should be included
            # Skip if this key already exists in entity_dict (to avoid overwriting manually set values)
            if display_attr_name not in entity_dict:
                if json_value is not None:
                    entity_dict[display_attr_name] = json_value
                elif INCLUDE_EMPTY_PROPERTIES:
                    entity_dict[display_attr_name] = ""
        
        # Generate deterministic GUID and place it as the first attribute
        if not 'componentGuid' in entity_dict and 'globalId' in entity_dict:
            # deterministic_guid = generateDeterministicGuid(
            #     self.modelName,
            #     entity_dict['componentType'],
            #     entity_dict['entityGuid']
            # )
            # # Create new dict with componentGuid first, then all existing entries
            ordered_dict = {'componentGuid': expandGuid(entity_dict['globalId']) }
            ordered_dict.update(entity_dict)
            entity_dict = ordered_dict

        return entity_dict

    def getAttributeValueNew(self, value):
        """Helper function to convert attribute values to JSON-serializable format"""
        if value is None:
            return None
        elif isinstance(value, ifcopenshell.entity_instance):
        #elif isinstance(value, ifcopenshell.entity_instance):
            return self.processEntry(value)
            # Check if this is an IfcObject that should be processed
            # if value.is_a('IfcObject'):
            #     return self.processEntry(value)
            # else:
            #     return self.createReferenceObject(value.__dict__, self.COMPACT)
        elif isinstance(value, tuple):
            return tuple(self.getAttributeValueNew(v) for v in value)
        else:
            return value
        
    def toObj(self, entity):
        """Convert IfcProduct to OBJ mesh

        parameters:
        entity: ifcopenshell ifcProduct instance

        Returns:
        string: OBJ string
        """

        if entity.Representation:
            try:
                shape = ifcopenshell.geom.create_shape(self.settings, entity)

                # Check if geometry has verts and faces attributes
                if not hasattr(shape.geometry, 'verts') or not hasattr(shape.geometry, 'faces'):
                    return None

                verts = shape.geometry.verts
                vertsList = [' '.join(map(str, verts[x:x+3]))
                             for x in range(0, len(verts), 3)]
                vertString = 'v ' + '\nv '.join(vertsList) + '\n'

                faces = shape.geometry.faces
                facesList = [' '.join(map(str, [f + 1 for f in faces[x:x+3]]))
                             for x in range(0, len(faces), 3)]
                faceString = 'f ' + '\nf '.join(map(str, facesList)) + '\n'

                return vertString + faceString
            except Exception as e:
                print(str(e) + ': Unable to generate OBJ data for ' +
                      str(entity))
                return None





def main():
    """Main entry point for processing IFC files"""
    parser = argparse.ArgumentParser(
        description='Convert IFC file to JSON format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ifc4ingestor.py input.ifc
  python ifc4ingestor.py input.ifc -o output.json
  python ifc4ingestor.py input.ifc --empty-properties
        """
    )
    
    parser.add_argument('input',
                        help='Input IFC file path')
    parser.add_argument('-o', '--output',
                        help='Output JSON file path (if not specified, prints to stdout)')
    parser.add_argument('--empty-properties',
                        action='store_true',
                        help='Include empty properties in output')
    
    args = parser.parse_args()


    
    # Check if input file exists
    import os
    if not os.path.isfile(args.input):
        print(f"Error: Input file '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)
    
    try:
        # Create converter instance
        converter = IFC2JSONSimple(
            args.input,
            EMPTY_PROPERTIES=args.empty_properties
        )
        
        # Convert to JSON
        json_objects = converter.spf2Json()
        
        # Prepare output
        output_data = json_objects
        
        # Output results
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2, default=str)
            print(f"Successfully wrote {len(json_objects)} entities to {args.output}")
        else:
            json_output = json.dumps(output_data, indent=2, default=str)
            print(json_output)
            
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
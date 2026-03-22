import uuid
import hashlib

try:
    import ifcopenshell.guid as guid
except ImportError:
    guid = None


def toLowerCamelcase(string):
    """Convert string from upper to lower camelCase"""
    return string[0].lower() + string[1:]

def expandGuid(entityGuid):
    if guid is None:
        raise RuntimeError("ifcopenshell not available - cannot expand GUID")
    try:
        expanded = guid.expand(entityGuid)
        # Format as UUID with dashes: 8-4-4-4-12
        return str(uuid.UUID(expanded))
    except Exception as e:
        # If GUID expansion fails (e.g., malformed GUID), return the original GUID
        print(f"Warning: Could not expand GUID '{entityGuid}': {e}. Using original value.")
        return entityGuid

def generateDeterministicGuid(modelName, componentType, entityGuid):
    """Generate a deterministic GUID based on model name, component type and entity GUID
    
    Parameters:
    modelName (str): The model name
    componentType (str): The component type string
    entityGuid (str): The entity GUID
    
    Returns:
    str: A GUID formatted as 01695e4-f7c6-46b0-8f70-8a0172df5a1
    """
    # Create a hash from the combination of modelName, componentType and entityGuid
    #hash_input = f"{modelName}:{componentType}:{entityGuid}".encode('utf-8')
    hash_input = f"{componentType}:{entityGuid}".encode('utf-8')
    hash_obj = hashlib.sha256(hash_input)
    hash_hex = hash_obj.hexdigest()
    
    # Create a UUID from the hash (using namespace and name approach)
    guid_obj = uuid.UUID(bytes=hash_obj.digest()[:16])
    guid_str = str(guid_obj)
    
    # Format as the required format (removing dashes and re-adding in specific positions)
    guid_clean = guid_str.replace('-', '')
    formatted_guid = f"{guid_clean[0:8]}-{guid_clean[8:12]}-{guid_clean[12:16]}-{guid_clean[16:20]}-{guid_clean[20:32]}"
    
    return formatted_guid
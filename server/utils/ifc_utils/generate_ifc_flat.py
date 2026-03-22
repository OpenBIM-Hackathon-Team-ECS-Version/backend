#!/usr/bin/env python3
"""
Generate flat IFC class list with attributes from IFCOpenShell and output as JSON.

This script queries IFCOpenShell's schema to extract all entity classes with their
attributes, organized alphabetically without hierarchy nesting.
"""

import json
import sys
from pathlib import Path

try:
    import ifcopenshell
    from ifcopenshell import ifcopenshell_wrapper
except ImportError:
    print("Error: ifcopenshell is not installed.")
    print("Install it with: pip install ifcopenshell")
    sys.exit(1)


def get_ifc_classes():
    """
    Extract all IFC entity classes from ifcopenshell schema.
    
    Returns:
        dict: Mapping of class names to their properties and attributes
    """
    classes = {}
    
    try:
        # Get the IFC4 schema definition
        print("Accessing IFC4 schema...")
        schema = ifcopenshell_wrapper.schema_by_name('IFC4')
        print(f"Schema: {schema}")
        
        # Get all entities
        entities = schema.entities()
        print(f"Found {len(entities)} entities in IFC4 schema")
        
        for i, entity in enumerate(entities):
            try:
                class_name = entity.name()
                
                # Get parent type (supertype)
                parent_name = None
                try:
                    supertype = entity.supertype()
                    if supertype:
                        parent_name = supertype.name()
                except Exception as e:
                    pass
                
                # Get attributes
                attributes = []
                try:
                    if hasattr(entity, 'all_attributes'):
                        all_attrs = entity.all_attributes()
                        for attr in all_attrs:
                            try:
                                attr_name = attr.name()
                                attributes.append(attr_name)
                            except:
                                pass
                except Exception as e:
                    pass
                
                classes[class_name] = {
                    'parent': parent_name,
                    'attributes': attributes
                }
                
                if (i + 1) % 100 == 0:
                    print(f"  Processed {i + 1} entities...")
        
            except Exception as e:
                print(f"Warning: Error processing entity {i}: {e}")
        
        print(f"Successfully extracted {len(classes)} IFC entity types")
    
    except Exception as e:
        print(f"Error accessing schema: {e}")
        import traceback
        traceback.print_exc()
    
    return classes


def create_flat_list(classes):
    """
    Create a flat, alphabetically sorted list of classes with attributes.
    
    Args:
        classes (dict): Mapping of class names to their properties
        
    Returns:
        list: Sorted list of class entries with name, parent, and attributes
    """
    flat_list = []
    
    for class_name in sorted(classes.keys()):
        class_info = classes[class_name]
        entry = {
            'name': class_name,
            'parent': class_info['parent'],
            'attributes': class_info['attributes']
        }
        flat_list.append(entry)
    
    return flat_list


def get_summary_stats(classes):
    """
    Generate summary statistics about the classes.
    
    Args:
        classes (dict): Mapping of class names to their properties
        
    Returns:
        dict: Summary statistics
    """
    total_classes = len(classes)
    
    # Count classes with no parent or parent not in schema
    root_classes = sum(
        1 for c in classes.values() 
        if c['parent'] is None or c['parent'] not in classes
    )
    
    # Count total attributes across all classes
    total_attributes = sum(len(c['attributes']) for c in classes.values())
    
    # Find class with most attributes
    max_attr_class = max(classes.items(), key=lambda x: len(x[1]['attributes']), default=(None, {}))
    
    return {
        'total_classes': total_classes,
        'root_classes': root_classes,
        'total_attributes': total_attributes,
        'max_attributes_class': max_attr_class[0],
        'max_attributes_count': len(max_attr_class[1].get('attributes', []))
    }


def main():
    """Main function to generate flat IFC class list JSON."""
    print("Querying IFCOpenShell for IFC class information...\n")
    
    # Get all IFC classes
    classes = get_ifc_classes()
    print(f"Found {len(classes)} IFC entity classes\n")
    
    # Create flat sorted list
    flat_list = create_flat_list(classes)
    print(f"Created alphabetically sorted list")
    
    # Get summary statistics
    stats = get_summary_stats(classes)
    
    # Create output structure
    output = {
        'metadata': {
            'generated_by': 'generate_ifc_flat.py',
            'ifcopenshell_version': ifcopenshell.version,
            'schema': 'IFC4',
            'statistics': stats
        },
        'classes': flat_list
    }
    
    # Output to JSON file
    output_file = Path(__file__).parent / 'IFC_Classes_Flat.json'
    
    print(f"\nWriting flat class list to {output_file}...")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Successfully generated {output_file}")
    print(f"\nSummary:")
    print(f"  Total Classes: {stats['total_classes']}")
    print(f"  Root Classes: {stats['root_classes']}")
    print(f"  Total Attributes: {stats['total_attributes']}")
    print(f"  Class with Most Attributes: {stats['max_attributes_class']} ({stats['max_attributes_count']} attributes)")


if __name__ == '__main__':
    main()

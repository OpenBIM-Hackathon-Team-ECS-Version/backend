#!/usr/bin/env python3
"""
Generate complete IFC class hierarchy from IFCOpenShell and output as nested JSON.

This script queries IFCOpenShell's schema to extract all entity classes and their
inheritance relationships, creating a comprehensive nested JSON representation of
the IFC class hierarchy.
"""

import json
import sys
import inspect
import tempfile
import os
from collections import defaultdict
from pathlib import Path

try:
    import ifcopenshell
    from ifcopenshell.entity_instance import entity_instance
except ImportError:
    print("Error: ifcopenshell is not installed.")
    print("Install it with: pip install ifcopenshell")
    sys.exit(1)


def get_ifc_classes():
    """
    Extract all IFC entity classes from ifcopenshell schema.
    
    Returns:
        dict: Mapping of class names to their properties and parent information
    """
    from ifcopenshell import ifcopenshell_wrapper
    
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
                
                # Get direct subtypes (children)
                subtypes_list = []
                try:
                    subtypes = entity.subtypes()
                    if subtypes:
                        subtypes_list = [st.name() for st in subtypes]
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
                    'attributes': attributes,
                    'children': []
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


def build_hierarchy(classes):
    """
    Build parent-child relationships in the class dictionary.
    
    Args:
        classes (dict): Mapping of class names to their properties
        
    Returns:
        dict: Updated classes with children relationships built
    """
    # Build parent-child relationships
    for class_name, class_info in classes.items():
        parent_name = class_info['parent']
        # If parent exists in our classes, add this class as a child
        if parent_name and parent_name in classes:
            classes[parent_name]['children'].append(class_name)
    
    return classes


def build_nested_tree(classes):
    """
    Build a nested tree structure starting from root classes.
    
    Args:
        classes (dict): Mapping of class names to their properties
        
    Returns:
        list: Root nodes of the hierarchy tree with nested children
    """
    # Find root classes (those without parents in our set)
    roots = []
    for class_name, class_info in classes.items():
        if class_info['parent'] is None or class_info['parent'] not in classes:
            roots.append(class_name)
    
    # Sort roots for consistent output
    roots.sort()
    
    def build_node(class_name):
        """Recursively build a nested node structure."""
        class_info = classes[class_name]
        
        # Build the node with nested structure (no attributes)
        node = {
            'name': class_name
        }
        
        # Add children if any - recursively build them
        if class_info['children']:
            node['children'] = []
            for child_name in sorted(class_info['children']):
                child_node = build_node(child_name)
                node['children'].append(child_node)
        
        return node
    
    # Build the tree starting from roots
    tree = [build_node(root) for root in roots]
    
    return tree


def get_summary_stats(classes):
    """
    Generate summary statistics about the class hierarchy.
    
    Args:
        classes (dict): Mapping of class names to their properties
        
    Returns:
        dict: Summary statistics
    """
    total_classes = len(classes)
    
    # Root classes are those without parents or whose parents don't exist in classes
    root_classes = sum(
        1 for c in classes.values() 
        if c['parent'] is None or c['parent'] not in classes
    )
    
    # Calculate max depth
    def get_depth(class_name, visited=None):
        if visited is None:
            visited = set()
        if class_name in visited:
            return 0
        visited.add(class_name)
        
        if class_name not in classes:
            return 0
            
        children = classes[class_name]['children']
        if not children:
            return 1
        return 1 + max(get_depth(child, visited.copy()) for child in children)
    
    # Get max depth from root classes
    root_list = [
        name for name, c in classes.items() 
        if c['parent'] is None or c['parent'] not in classes
    ]
    
    max_depth = max(
        (get_depth(name) for name in root_list),
        default=0
    )
    
    return {
        'total_classes': total_classes,
        'root_classes': root_classes,
        'max_depth': max_depth
    }


def output_text_tree(classes):
    """
    Output the class hierarchy as indented text with tabs showing the tree structure.
    
    Args:
        classes (dict): Mapping of class names to their properties with children relationships
    """
    # Find root classes
    roots = []
    for class_name, class_info in classes.items():
        if class_info['parent'] is None or class_info['parent'] not in classes:
            roots.append(class_name)
    
    roots.sort()
    
    def write_tree(class_name, depth=0, file_obj=None):
        """Recursively write the tree structure."""
        indent = '\t' * depth
        line = f"{indent}{class_name}\n"
        
        if file_obj:
            file_obj.write(line)
        else:
            print(line, end='')
        
        # Write children
        children = sorted(classes[class_name]['children'])
        for child in children:
            write_tree(child, depth + 1, file_obj)
    
    # Output to file
    output_file = Path(__file__).parent / 'IFC_Classes_Tree.txt'
    
    print(f"Writing text tree hierarchy to {output_file}...", file=sys.stderr)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for root in roots:
            write_tree(root, 0, f)
    
    # Also print to console
    print("\nIFC Class Hierarchy (first 50 lines):\n", file=sys.stderr)
    lines_printed = 0
    for root in roots:
        if lines_printed >= 50:
            print("...\n(See IFC_Classes_Tree.txt for complete hierarchy)", file=sys.stderr)
            break
        
        def count_lines(class_name, depth=0):
            nonlocal lines_printed
            if lines_printed >= 50:
                return
            lines_printed += 1
            indent = '\t' * depth
            print(f"{indent}{class_name}")
            for child in sorted(classes[class_name]['children']):
                count_lines(child, depth + 1)
        
        count_lines(root)
    
    print(f"\n✓ Successfully generated {output_file}", file=sys.stderr)
    print(f"Total lines: {sum(1 for root in roots for _ in enumerate_tree(root, classes))}", file=sys.stderr)


def enumerate_tree(class_name, classes, visited=None):
    """Helper to count tree nodes."""
    if visited is None:
        visited = set()
    if class_name in visited:
        return
    visited.add(class_name)
    
    yield class_name
    for child in classes[class_name]['children']:
        yield from enumerate_tree(child, classes, visited)


def main():
    """Main function to generate IFC class hierarchy JSON."""
    print("Querying IFCOpenShell for IFC class hierarchy...")
    
    # Parse arguments
    text_tree = '--text-tree' in sys.argv
    
    # Get all IFC classes
    classes = get_ifc_classes()
    print(f"Found {len(classes)} IFC entity classes")
    
    # Build relationships
    classes = build_hierarchy(classes)
    print("Built parent-child relationships")
    
    # If text-tree output requested
    if text_tree:
        output_text_tree(classes)
        return
    
    # Build nested tree structure
    tree = build_nested_tree(classes)
    print(f"Created nested hierarchy with {len(tree)} root classes")
    
    # Get summary statistics
    stats = get_summary_stats(classes)
    
    # Create output structure
    output = {
        'metadata': {
            'generated_by': 'generate_ifc_hierarchy.py',
            'ifcopenshell_version': ifcopenshell.version,
            'schema': 'IFC4',
            'statistics': stats
        },
        'classes': tree
    }
    
    # Output to JSON file
    output_file = Path(__file__).parent / 'IFC_Classes.json'
    
    print(f"\nWriting nested hierarchy to {output_file}...")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Successfully generated {output_file}")
    print(f"\nSummary:")
    print(f"  Total Classes: {stats['total_classes']}")
    print(f"  Root Classes: {stats['root_classes']}")
    print(f"  Maximum Depth: {stats['max_depth']}")


if __name__ == '__main__':
    main()

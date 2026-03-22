#!/usr/bin/env python3
"""
IFC Schema Generator

Given a list of root classes, generates a flat JSON schema containing all classes
(root + descendants) with their attributes organized as a JSON schema.
"""

import json
import sys
from typing import Dict, Set, List, Optional

try:
    import ifcopenshell
    from ifcopenshell import ifcopenshell_wrapper
except ImportError:
    print("Error: ifcopenshell is not installed.")
    print("Install it with: pip install ifcopenshell")
    sys.exit(1)


class IFCSchemaGenerator:
    """Generate JSON schema from IFC classes."""
    
    def __init__(self):
        """Initialize by loading IFC4 schema."""
        self.classes: Dict[str, Dict] = {}  # class_name -> {parent, attributes}
        self.children_map: Dict[str, Set[str]] = {}  # class_name -> set of direct children
        self._load_schema()
    
    def _load_schema(self):
        """Load the IFC4 schema and build hierarchy."""
        try:
            schema = ifcopenshell_wrapper.schema_by_name('IFC4')
            entities = schema.entities()
            
            for entity in entities:
                class_name = entity.name()
                
                # Get parent
                parent_name = None
                try:
                    supertype = entity.supertype()
                    if supertype:
                        parent_name = supertype.name()
                except:
                    pass
                
                # Get detailed attributes with type and constraint information
                attributes = []
                try:
                    if hasattr(entity, 'all_attributes'):
                        all_attrs = entity.all_attributes()
                        for attr in all_attrs:
                            try:
                                attr_name = attr.name()
                                attr_type_str = str(attr.type_of_attribute())
                                optional = attr.optional()
                                
                                # Try to extract derived status
                                derived = False
                                try:
                                    derived = attr.derived()
                                except:
                                    pass
                                
                                attr_info = {
                                    'name': attr_name,
                                    'type': attr_type_str,
                                    'optional': optional,
                                    'derived': derived
                                }
                                
                                attributes.append(attr_info)
                            except:
                                pass
                except:
                    pass
                
                self.classes[class_name] = {
                    'parent': parent_name,
                    'attributes': attributes
                }
                
                # Initialize children set
                if class_name not in self.children_map:
                    self.children_map[class_name] = set()
                
                # Add to parent's children
                if parent_name:
                    if parent_name not in self.children_map:
                        self.children_map[parent_name] = set()
                    self.children_map[parent_name].add(class_name)
        
        except Exception as e:
            print(f"Error loading schema: {e}", file=sys.stderr)
            sys.exit(1)
    
    def get_descendants(self, class_name: str) -> List[str]:
        """Get all descendants of a class including itself."""
        if class_name not in self.classes:
            raise ValueError(f"Class '{class_name}' not found in IFC schema")
        
        descendants = set()
        to_process = [class_name]
        
        while to_process:
            current = to_process.pop(0)
            
            if current not in descendants:
                descendants.add(current)
                
                # Add all direct children
                children = self.children_map.get(current, set())
                for child in children:
                    if child not in descendants:
                        to_process.append(child)
        
        return sorted(list(descendants))
    
    def generate_schema(self, root_classes: List[str]) -> Dict:
        """
        Generate a flat schema from root classes and their descendants.
        
        Args:
            root_classes: List of root class names
            
        Returns:
            Dictionary with schema information
        """
        all_classes = set()
        
        # Collect all classes (root + descendants)
        for root_class in root_classes:
            try:
                descendants = self.get_descendants(root_class)
                all_classes.update(descendants)
            except ValueError as e:
                print(f"Warning: {e}", file=sys.stderr)
        
        # Build the schema entries
        schema_entries = []
        
        for class_name in sorted(all_classes):
            class_info = self.classes[class_name]
            
            entry = {
                'name': class_name,
                'parent': class_info['parent'],
                'attributes': class_info['attributes']
            }
            
            schema_entries.append(entry)
        
        return {
            'root_classes': root_classes,
            'total_classes': len(schema_entries),
            'classes': schema_entries
        }


def parse_arguments() -> tuple:
    """Parse command line arguments."""
    if len(sys.argv) < 2:
        print("Usage: python ifc_schema_generator.py <root_class1> [root_class2] ... [--output FILE] [--pretty]")
        print("\nExamples:")
        print("  python ifc_schema_generator.py IfcElement")
        print("  python ifc_schema_generator.py IfcElement IfcProduct")
        print("  python ifc_schema_generator.py IfcElement --output schema.json --pretty")
        sys.exit(1)
    
    root_classes = []
    output_file = None
    pretty = False
    
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        
        if arg == '--output' and i + 1 < len(sys.argv):
            output_file = sys.argv[i + 1]
            i += 2
        elif arg == '--pretty':
            pretty = True
            i += 1
        elif not arg.startswith('--'):
            root_classes.append(arg)
            i += 1
        else:
            i += 1
    
    return root_classes, output_file, pretty


def main():
    """Main function."""
    
    root_classes, output_file, pretty = parse_arguments()
    
    if not root_classes:
        print("Error: No root classes specified", file=sys.stderr)
        sys.exit(1)
    
    # Initialize generator
    print(f"Loading IFC4 schema...", file=sys.stderr)
    generator = IFCSchemaGenerator()
    
    try:
        # Generate schema
        print(f"Generating schema for root classes: {', '.join(root_classes)}", file=sys.stderr)
        schema = generator.generate_schema(root_classes)
        
        # Format output
        if pretty:
            output = json.dumps(schema, indent=2)
        else:
            output = json.dumps(schema)
        
        # Output to file or stdout
        if output_file:
            print(f"Writing schema to {output_file}...", file=sys.stderr)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(output)
            print(f"✓ Schema written to {output_file}", file=sys.stderr)
        else:
            print(output)
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
IFC Class Descendants Exporter

Given a base class name, exports all descendants (including the base class itself)
as a JSON array.
"""

import json
import sys
from typing import Dict, Set, List

try:
    import ifcopenshell
    from ifcopenshell import ifcopenshell_wrapper
except ImportError:
    print("Error: ifcopenshell is not installed.")
    print("Install it with: pip install ifcopenshell")
    sys.exit(1)


class IFCDescendantsExporter:
    """Export all descendants of an IFC class."""
    
    def __init__(self):
        """Initialize by loading IFC4 schema."""
        self.classes: Dict[str, str] = {}  # class_name -> parent_name
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
                
                self.classes[class_name] = parent_name
                
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
        """
        Get all descendants of a class (including the class itself).
        
        Args:
            class_name: The base class name (e.g., 'IfcElement')
            
        Returns:
            Sorted list of all descendant class names including the base class
        """
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


def main():
    """Main function."""
    
    if len(sys.argv) < 2:
        print("Usage: python ifc_descendants_export.py <base_class> [--pretty] [--component]")
        print("\nExamples:")
        print("  python ifc_descendants_export.py IfcElement")
        print("  python ifc_descendants_export.py IfcElement --pretty")
        print("  python ifc_descendants_export.py IfcWall --component")
        print("  python ifc_descendants_export.py IfcWall --pretty --component")
        sys.exit(1)
    
    base_class = sys.argv[1]
    pretty = '--pretty' in sys.argv
    component = '--component' in sys.argv
    
    # Initialize exporter
    exporter = IFCDescendantsExporter()
    
    try:
        # Get all descendants
        descendants = exporter.get_descendants(base_class)
        
        # Append "Component" suffix if requested
        if component:
            descendants = [f"{cls}Component" for cls in descendants]
        
        # Output JSON array directly
        if pretty:
            print(json.dumps(descendants, indent=2))
        else:
            print(json.dumps(descendants))
    
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

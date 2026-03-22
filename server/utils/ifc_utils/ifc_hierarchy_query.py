#!/usr/bin/env python3
"""
IFC Class Hierarchy Query Tool

Checks if a class is a subclass (directly or indirectly) of a base class
in the IFC4 schema using IFCOpenShell.
"""

import sys
from typing import Dict, Optional, Set

try:
    import ifcopenshell
    from ifcopenshell import ifcopenshell_wrapper
except ImportError:
    print("Error: ifcopenshell is not installed.")
    print("Install it with: pip install ifcopenshell")
    sys.exit(1)


class IFCHierarchy:
    """Query tool for IFC class hierarchy relationships."""
    
    def __init__(self):
        """Initialize the hierarchy by loading IFC4 schema."""
        self.classes: Dict[str, Optional[str]] = {}  # class_name -> parent_name
        self.children_map: Dict[str, Set[str]] = {}  # class_name -> set of direct children
        self._load_schema()
    
    def _load_schema(self):
        """Load the IFC4 schema and build hierarchy."""
        print("Loading IFC4 schema...")
        try:
            schema = ifcopenshell_wrapper.schema_by_name('IFC4')
            entities = schema.entities()
            
            for i, entity in enumerate(entities):
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
                
                if (i + 1) % 200 == 0:
                    print(f"  Loaded {i + 1} classes...")
            
            print(f"✓ Loaded {len(self.classes)} IFC classes\n")
        
        except Exception as e:
            print(f"Error loading schema: {e}")
            sys.exit(1)
    
    def is_subclass_of(self, test_class: str, base_class: str) -> bool:
        """
        Check if test_class is a subclass of base_class (including itself).
        
        Args:
            test_class: The class to check (e.g., 'IfcWall')
            base_class: The base class to check against (e.g., 'IfcElement')
            
        Returns:
            True if test_class is base_class or a subclass of base_class, False otherwise
        """
        # Check if both classes exist
        if test_class not in self.classes:
            return False
        if base_class not in self.classes:
            return False
        
        # Check if they're the same class
        if test_class == base_class:
            return True
        
        # Walk up the hierarchy from test_class
        current = test_class
        visited = set()
        
        while current:
            if current in visited:
                # Prevent infinite loops (shouldn't happen in valid schema)
                break
            visited.add(current)
            
            # Get parent
            parent = self.classes.get(current)
            
            if parent == base_class:
                return True
            
            current = parent
        
        return False
    
    def get_parent(self, class_name: str) -> Optional[str]:
        """Get the direct parent class of a class."""
        return self.classes.get(class_name)
    
    def get_all_parents(self, class_name: str) -> list:
        """Get all parent classes up to the root."""
        parents = []
        current = class_name
        visited = set()
        
        while current:
            if current in visited:
                break
            visited.add(current)
            
            parent = self.classes.get(current)
            if parent:
                parents.append(parent)
                current = parent
            else:
                current = None
        
        return parents
    
    def get_children(self, class_name: str) -> list:
        """Get direct children of a class."""
        return sorted(list(self.children_map.get(class_name, set())))
    
    def get_all_subclasses(self, class_name: str) -> list:
        """Get all subclasses recursively."""
        if class_name not in self.classes:
            return []
        
        subclasses = []
        to_process = [class_name]
        visited = set()
        
        while to_process:
            current = to_process.pop(0)
            
            if current in visited:
                continue
            visited.add(current)
            
            children = self.get_children(current)
            for child in children:
                if child != class_name:  # Don't include the class itself
                    subclasses.append(child)
                    to_process.append(child)
        
        return sorted(subclasses)
    
    def get_hierarchy_path(self, class_name: str) -> list:
        """Get the complete path from root to this class."""
        parents = self.get_all_parents(class_name)
        # Reverse to get from root to class
        path = list(reversed(parents)) + [class_name]
        return path


def main():
    """Main function for CLI usage."""
    
    # Initialize hierarchy
    hierarchy = IFCHierarchy()
    
    # Check if running in interactive mode
    if len(sys.argv) == 1:
        # Interactive mode
        print("IFC Hierarchy Query Tool")
        print("=" * 50)
        print("\nCommands:")
        print("  issubclass <class> <base>  - Check if class is subclass of base")
        print("  parent <class>             - Get direct parent")
        print("  parents <class>            - Get all parent classes")
        print("  children <class>           - Get direct children")
        print("  subclasses <class>         - Get all subclasses")
        print("  path <class>               - Get path from root to class")
        print("  help                       - Show this help")
        print("  quit                       - Exit\n")
        
        while True:
            try:
                user_input = input("> ").strip()
                
                if not user_input:
                    continue
                
                parts = user_input.split()
                command = parts[0].lower()
                
                if command == 'quit':
                    break
                
                elif command == 'help':
                    print("\nCommands:")
                    print("  issubclass <class> <base>  - Check if class is subclass of base")
                    print("  parent <class>             - Get direct parent")
                    print("  parents <class>            - Get all parent classes")
                    print("  children <class>           - Get direct children")
                    print("  subclasses <class>         - Get all subclasses")
                    print("  path <class>               - Get path from root to class")
                    print("  help                       - Show this help")
                    print("  quit                       - Exit\n")
                
                elif command == 'issubclass' and len(parts) == 3:
                    test_class = parts[1]
                    base_class = parts[2]
                    result = hierarchy.is_subclass_of(test_class, base_class)
                    print(f"{test_class} is {'a subclass of' if result else 'NOT a subclass of'} {base_class}\n")
                
                elif command == 'parent' and len(parts) == 2:
                    class_name = parts[1]
                    parent = hierarchy.get_parent(class_name)
                    if parent:
                        print(f"Parent of {class_name}: {parent}\n")
                    else:
                        print(f"{class_name}: No parent (root class)\n")
                
                elif command == 'parents' and len(parts) == 2:
                    class_name = parts[1]
                    parents = hierarchy.get_all_parents(class_name)
                    if parents:
                        print(f"All parents of {class_name}:")
                        for parent in parents:
                            print(f"  - {parent}")
                        print()
                    else:
                        print(f"{class_name} is a root class\n")
                
                elif command == 'children' and len(parts) == 2:
                    class_name = parts[1]
                    children = hierarchy.get_children(class_name)
                    if children:
                        print(f"Direct children of {class_name} ({len(children)}):")
                        for child in children[:20]:  # Show first 20
                            print(f"  - {child}")
                        if len(children) > 20:
                            print(f"  ... and {len(children) - 20} more")
                        print()
                    else:
                        print(f"{class_name} has no direct children\n")
                
                elif command == 'subclasses' and len(parts) == 2:
                    class_name = parts[1]
                    subclasses = hierarchy.get_all_subclasses(class_name)
                    if subclasses:
                        print(f"All subclasses of {class_name} ({len(subclasses)}):")
                        for subclass in subclasses[:20]:  # Show first 20
                            print(f"  - {subclass}")
                        if len(subclasses) > 20:
                            print(f"  ... and {len(subclasses) - 20} more")
                        print()
                    else:
                        print(f"{class_name} has no subclasses\n")
                
                elif command == 'path' and len(parts) == 2:
                    class_name = parts[1]
                    path = hierarchy.get_hierarchy_path(class_name)
                    print(f"Path to {class_name}:")
                    for i, cls in enumerate(path):
                        print(f"  {'  ' * i}└─ {cls}")
                    print()
                
                else:
                    print("Unknown command or incorrect arguments. Type 'help' for usage.\n")
            
            except KeyboardInterrupt:
                print("\nExiting...")
                break
            except Exception as e:
                print(f"Error: {e}\n")
    
    else:
        # Command line mode
        if sys.argv[1] == 'issubclass' and len(sys.argv) == 4:
            test_class = sys.argv[2]
            base_class = sys.argv[3]
            result = hierarchy.is_subclass_of(test_class, base_class)
            print(f"{test_class} is {'a subclass of' if result else 'NOT a subclass of'} {base_class}")
            sys.exit(0 if result else 1)
        
        else:
            print("Usage: python ifc_hierarchy_query.py [issubclass <class> <base>]")
            print("Or run without arguments for interactive mode")
            sys.exit(1)


if __name__ == '__main__':
    main()

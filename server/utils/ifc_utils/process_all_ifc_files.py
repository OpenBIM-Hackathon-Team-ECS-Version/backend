#!/usr/bin/env python3
"""
Batch IFC to JSON Processor with Component Extraction

This script processes all IFC files in the data folder and its subdirectories,
generating corresponding JSON files using the ifc4ingestor.py module.

Optionally, with the --extract-components flag, it can extract individual
component files named by entityGuid_componentGuid into an output folder.
"""

import os
import sys
import json
import shutil
from pathlib import Path

# Add server directory to Python path to import the ingestor
server_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(server_dir))

# Add ingestors directory to path (needed for utils.py import)
ingestors_dir = server_dir / 'ingestors'
sys.path.insert(0, str(ingestors_dir))

from ifc4ingestor import IFC2JSONSimple


def find_ifc_files(root_dir):
    """
    Recursively find all IFC files in the given directory.
    
    Args:
        root_dir (str or Path): Root directory to search
        
    Returns:
        list: List of Path objects for each IFC file found
    """
    root_path = Path(root_dir)
    ifc_files = []
    
    for file_path in root_path.rglob("*.ifc"):
        ifc_files.append(file_path)
    
    return sorted(ifc_files)


def process_ifc_file(ifc_path, output_path=None, compact=False, empty_properties=False):
    """
    Process a single IFC file and generate JSON output.
    
    Args:
        ifc_path (Path): Path to the IFC file
        output_path (Path): Path for the output JSON file (default: same as IFC with .json extension)
        compact (bool): If True, output compact JSON without pretty printing
        empty_properties (bool): If True, include empty properties in output
        
    Returns:
        tuple: (success: bool, num_entities: int, json_data: dict or None)
    """
    try:
        # Generate output path if not provided
        if output_path is None:
            output_path = ifc_path.with_suffix('.json')
        
        print(f"Processing: {ifc_path}")
        
        # Extract model name from parent directory
        model_name = ifc_path.parent.name
        
        # Create converter instance
        converter = IFC2JSONSimple(
            str(ifc_path),
            EMPTY_PROPERTIES=empty_properties,
            modelName=model_name
        )
        
        # Convert to JSON
        json_objects = converter.spf2Json()
        
        # Write to output file
        with open(output_path, 'w') as f:
            json.dump(json_objects, f, indent=None if compact else 2, default=str)
        
        print(f"  ✓ Generated: {output_path} ({len(json_objects)} entities)")
        return True, len(json_objects), json_objects
        
    except Exception as e:
        print(f"  ✗ Error processing {ifc_path}: {e}")
        import traceback
        traceback.print_exc()
        return False, 0, None


def extract_components(json_data, ifc_filename, output_folder):
    """
    Extract individual components from JSON data into separate files.
    
    Creates a directory named after the IFC file (without extension) and stores
    each component as entityGuid_componentGuid.json
    
    Args:
        json_data (list): List of component dictionaries from JSON file
        ifc_filename (str): Name of the source IFC file (used to create directory)
        output_folder (Path): Path to the output folder
        
    Returns:
        tuple: (success: bool, count: int)
    """
    try:
        # Create directory name from IFC filename (remove extension)
        dir_name = Path(ifc_filename).stem
        dir_path = output_folder / dir_name
        
        # If directory exists, empty it; otherwise create it
        if dir_path.exists():
            print(f"  Emptying existing directory: {dir_path}")
            shutil.rmtree(dir_path)
        
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"  Extracting components to: {dir_path}")
        
        # Store each component as a separate file
        stored_count = 0
        
        if not isinstance(json_data, list):
            print(f"  ✗ Expected list of components, got {type(json_data)}")
            return False, 0
        
        for component in json_data:
            # Get entityGuid and componentGuid from component
            entity_guid = component.get('entityGuid', 'unknown')
            component_guid = component.get('componentGuid', 'unknown')
            
            # Create filename: entityGuid_componentGuid.json
            component_filename = f"{entity_guid}_{component_guid}.json"
            component_path = dir_path / component_filename
            
            # Write component to file
            try:
                with open(component_path, 'w') as f:
                    json.dump(component, f, indent=2, default=str)
                stored_count += 1
            except Exception as e:
                print(f"    ✗ Error storing component {component_filename}: {e}")
        
        print(f"  ✓ Extracted {stored_count} components")
        return True, stored_count
        
    except Exception as e:
        print(f"  ✗ Error extracting components: {e}")
        return False, 0


def main():
    """Main function to process all IFC files in the data directory."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Process all IFC files in the data folder and generate JSON files'
    )
    parser.add_argument(
        '--data-dir',
        default='data',
        help='Root data directory containing IFC files (default: data)'
    )
    parser.add_argument(
        '--compact',
        action='store_true',
        help='Generate compact JSON without pretty printing'
    )
    parser.add_argument(
        '--empty-properties',
        action='store_true',
        help='Include empty properties in output'
    )
    parser.add_argument(
        '--extract-components',
        help='Output folder for extracted component files (optional). Creates subdirectories named after each IFC file'
    )
    parser.add_argument(
        '--skip-errors',
        action='store_true',
        help='Skip files with errors and continue processing (default: exit on error)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show which files would be processed without actually processing them'
    )
    
    args = parser.parse_args()
    
    # Resolve data directory path (relative to project root)
    project_root = Path(__file__).parent.parent.parent.parent
    data_dir = project_root / args.data_dir
    
    if not data_dir.exists():
        print(f"Error: Data directory '{data_dir}' does not exist.")
        sys.exit(1)
    
    # Validate output directory if specified
    output_dir = None
    if args.extract_components:
        output_dir = Path(args.extract_components)
        output_dir.mkdir(parents=True, exist_ok=True)
        if not output_dir.is_dir():
            print(f"Error: Output path '{output_dir}' is not a directory.")
            sys.exit(1)
    
    # Find all IFC files
    print(f"Searching for IFC files in: {data_dir}")
    print("-" * 70)
    
    ifc_files = find_ifc_files(data_dir)
    
    if not ifc_files:
        print("No IFC files found.")
        sys.exit(0)
    
    print(f"Found {len(ifc_files)} IFC file(s):\n")
    for ifc_file in ifc_files:
        print(f"  • {ifc_file.relative_to(project_root)}")
    
    print("\n" + "=" * 70)
    
    if args.dry_run:
        print("Dry run mode - no files will be processed.")
        sys.exit(0)
    
    # Process each IFC file
    print(f"Processing IFC files...\n")
    
    success_count = 0
    failure_count = 0
    total_entities = 0
    total_components_extracted = 0
    
    for ifc_file in ifc_files:
        success, num_entities, json_data = process_ifc_file(ifc_file, compact=args.compact, empty_properties=args.empty_properties)
        if success:
            success_count += 1
            total_entities += num_entities
            
            # Extract components if output directory specified and JSON data available
            if args.extract_components and json_data:
                extract_success, num_extracted = extract_components(json_data, ifc_file.name, output_dir)
                if extract_success:
                    total_components_extracted += num_extracted
        else:
            failure_count += 1
    
    # Summary
    print("\n" + "=" * 70)
    print(f"Processing complete!")
    print(f"  Successfully processed: {success_count}")
    print(f"  Failed: {failure_count}")
    print(f"  Total IFC files: {len(ifc_files)}")
    print(f"  Total entities: {total_entities}")
    
    if args.extract_components:
        print(f"  Total components extracted: {total_components_extracted}")
        print(f"  Output directory: {output_dir.resolve()}")


if __name__ == '__main__':
    main()

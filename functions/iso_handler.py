"""
ISO Handler module for Tomba2Edit
Handles extraction of TOMBA2.DAT, TOMBA2.IDX, TOMBA2.IMG from PlayStation ISO files
"""

import os
import struct
import shutil
import tempfile


class ISOHandler:
    """Handles extraction of files from PlayStation ISO images."""

    def __init__(self):
        self.temp_dir = None
        self.extracted_files = {}

    def extract_iso(self, iso_path):
        """Extract TOMBA2.DAT, TOMBA2.IDX, TOMBA2.IMG from ISO."""
        # Create temporary directory
        self.temp_dir = tempfile.mkdtemp(prefix="tomba2edit_")

        try:
            with open(iso_path, 'rb') as f:
                iso_data = f.read()

            print(f"ISO size: {len(iso_data)} bytes")

            # Find and parse the Primary Volume Descriptor
            pvd_offset = self.find_pvd(iso_data)
            if pvd_offset is None:
                raise Exception("Could not find Primary Volume Descriptor")

            print(f"PVD found at offset: {hex(pvd_offset)}")

            # Parse the root directory
            root_dir_lba, root_dir_size = self.parse_pvd(iso_data, pvd_offset)
            print(f"Root directory LBA: {root_dir_lba}, Size: {root_dir_size}")

            # Parse the root directory to find files
            files_found = self.parse_root_directory(iso_data, root_dir_lba, root_dir_size)

            # Check if we found all required files
            required_files = ["TOMBA2.DAT", "TOMBA2.IDX", "TOMBA2.IMG"]
            missing_files = [f for f in required_files if f not in files_found]

            if missing_files:
                # Try fallback extraction methods
                print(f"Missing files: {missing_files}")
                files_found = self.fallback_extraction(iso_path, iso_data, required_files, files_found)

            # Check again
            missing_files = [f for f in required_files if f not in files_found]
            if missing_files:
                raise FileNotFoundError(f"Could not find: {', '.join(missing_files)}")

            self.extracted_files = files_found
            return files_found

        except Exception as e:
            # Clean up on error
            self.cleanup()
            raise e

    def find_pvd(self, iso_data):
        """Find the Primary Volume Descriptor in the ISO."""
        # Check standard location first (sector 16, offset 0x8000)
        pvd_offset = 0x8000
        if pvd_offset + 2048 <= len(iso_data):
            pvd_data = iso_data[pvd_offset:pvd_offset + 2048]
            if pvd_data[0:5] == b'CD001':
                return pvd_offset

        # Search for CD001 signature
        cd001_pos = iso_data.find(b'CD001')
        if cd001_pos != -1:
            # CD001 is at byte 1, so PVD starts at cd001_pos - 1
            pvd_offset = cd001_pos - 1
            if pvd_offset >= 0 and pvd_offset + 2048 <= len(iso_data):
                pvd_data = iso_data[pvd_offset:pvd_offset + 2048]
                if pvd_data[0:5] == b'CD001':
                    return pvd_offset

        # Search for the volume descriptor set terminator
        # This indicates the end of the PVD, we need to go back
        terminator = iso_data.find(b'CD001' + b'\xff' * 2042)
        if terminator != -1:
            # Go back to find the PVD
            for offset in range(terminator - 2048 * 10, terminator, 2048):
                if offset >= 0:
                    pvd_data = iso_data[offset:offset + 2048]
                    if pvd_data[0:5] == b'CD001' and pvd_data[6:7] == b'\x01':
                        return offset

        return None

    def parse_pvd(self, iso_data, pvd_offset):
        """Parse the Primary Volume Descriptor to get root directory info."""
        pvd_data = iso_data[pvd_offset:pvd_offset + 2048]

        # Root directory record starts at offset 156 in PVD
        root_dir_record = pvd_data[156:156 + 34]

        # Location of extent (bytes 2-5, little endian)
        root_dir_lba = struct.unpack('<I', root_dir_record[2:6])[0]

        # Size of directory (bytes 10-13, little endian)
        root_dir_size = struct.unpack('<I', root_dir_record[10:14])[0]

        return root_dir_lba, root_dir_size

    def parse_root_directory(self, iso_data, root_dir_lba, root_dir_size):
        """Parse the root directory to find TOMBA2 files."""
        root_dir_offset = root_dir_lba * 2048
        root_dir_data = iso_data[root_dir_offset:root_dir_offset + root_dir_size]

        files_found = {}
        offset = 0

        while offset < len(root_dir_data):
            # Get directory record length
            record_len = root_dir_data[offset]
            if record_len == 0:
                break

            # File name length
            name_len = root_dir_data[offset + 32]
            if name_len > 0:
                # Get file name (extract from record)
                name_start = offset + 33
                file_name_bytes = root_dir_data[name_start:name_start + name_len]

                try:
                    file_name = file_name_bytes.decode('ascii', errors='ignore').strip()
                except:
                    file_name = ''

                # Check if this is one of our files
                if file_name.upper() in ['TOMBA2.DAT', 'TOMBA2.IDX', 'TOMBA2.IMG']:
                    # Get file location and size
                    file_lba = struct.unpack('<I', root_dir_data[offset + 2:offset + 6])[0]
                    file_size = struct.unpack('<I', root_dir_data[offset + 10:offset + 14])[0]

                    print(f"Found {file_name}: LBA={file_lba}, Size={file_size}")

                    # Extract file data
                    file_offset = file_lba * 2048
                    file_data = iso_data[file_offset:file_offset + file_size]

                    # Save file
                    dest_path = os.path.join(self.temp_dir, file_name.upper())
                    with open(dest_path, 'wb') as out:
                        out.write(file_data)

                    files_found[file_name.upper()] = dest_path
                    print(f"Extracted: {file_name}")

            # Move to next record
            offset += record_len

        return files_found

    def fallback_extraction(self, iso_path, iso_data, required_files, files_found):
        """Try fallback methods to extract files."""
        print("Attempting fallback extraction...")

        # Method 1: Search for files in the ISO using patterns
        for filename in required_files:
            if filename in files_found:
                continue

            print(f"Searching for {filename} in ISO...")

            # Search for the file name in the ISO
            name_bytes = filename.encode('ascii')
            positions = []
            start = 0
            while True:
                pos = iso_data.find(name_bytes, start)
                if pos == -1:
                    break
                positions.append(pos)
                start = pos + 1

            print(f"Found {filename} at positions: {[hex(p) for p in positions[:5]]}")

            # Try each position to find the file data
            for pos in positions[:10]:  # Limit search to first 10 positions
                try:
                    # Look for directory entry around this position
                    sector_size = 2048
                    sector_start = (pos // sector_size) * sector_size
                    sector_end = min(sector_start + sector_size, len(iso_data))

                    # Search in this sector for the directory entry
                    sector_data = iso_data[sector_start:sector_end]
                    name_pos = sector_data.find(name_bytes)

                    if name_pos != -1:
                        # Try to find the directory entry
                        # The entry starts at name_pos - (name_len + 1)
                        # Usually the format is: record_len (1 byte), etc.
                        for offset in range(max(0, name_pos - 40), name_pos):
                            if sector_data[offset] > 0 and sector_data[offset] < 255:
                                # Check if this looks like a directory entry
                                if offset + 33 + name_len < len(sector_data):
                                    entry_name = sector_data[offset + 33:offset + 33 + name_len]
                                    if entry_name == name_bytes:
                                        # Found the entry
                                        file_lba = struct.unpack('<I', sector_data[offset + 2:offset + 6])[0]
                                        file_size = struct.unpack('<I', sector_data[offset + 10:offset + 14])[0]

                                        print(f"Found {filename} at LBA {file_lba}, Size {file_size}")

                                        # Extract the file
                                        file_offset = file_lba * 2048
                                        if file_offset + file_size <= len(iso_data):
                                            file_data = iso_data[file_offset:file_offset + file_size]
                                            dest_path = os.path.join(self.temp_dir, filename)
                                            with open(dest_path, 'wb') as out:
                                                out.write(file_data)
                                            files_found[filename] = dest_path
                                            print(f"Extracted: {filename}")
                                            break
                except Exception as e:
                    print(f"Error at position {pos}: {e}")
                    continue

        # Method 2: Try 7-Zip if available
        if len(files_found) < len(required_files):
            print("Trying 7-Zip extraction...")
            if self.extract_with_7zip(iso_path):
                # Check if files were extracted
                for root, dirs, file_list in os.walk(self.temp_dir):
                    for f in file_list:
                        if f.upper() in required_files and f.upper() not in files_found:
                            dest_path = os.path.join(self.temp_dir, f.upper())
                            shutil.copy2(os.path.join(root, f), dest_path)
                            files_found[f.upper()] = dest_path
                            print(f"Found {f.upper()} via 7-Zip")

        return files_found

    def extract_with_7zip(self, iso_path):
        """Extract ISO using 7-Zip if available."""
        seven_zip = shutil.which('7z') or shutil.which('7za')
        if seven_zip:
            print(f"Found 7-Zip at: {seven_zip}")
            try:
                import subprocess
                cmd = [seven_zip, 'x', iso_path, f'-o{self.temp_dir}', '-y']
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    shell=False
                )
                if result.returncode == 0:
                    print("7-Zip extraction succeeded!")
                    return True
                else:
                    print(f"7-Zip extraction failed: {result.stderr}")
            except Exception as e:
                print(f"7-Zip error: {e}")
        else:
            print("7-Zip not found")
        return False

    def get_temp_dir(self):
        """Get the temporary directory path."""
        return self.temp_dir

    def get_file_path(self, filename):
        """Get the path of an extracted file."""
        return self.extracted_files.get(filename.upper())

    def cleanup(self):
        """Clean up temporary files."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"Warning: Could not clean up temp directory: {e}")
        self.temp_dir = None
        self.extracted_files = {}
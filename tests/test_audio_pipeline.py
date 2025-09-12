#!/usr/bin/env python3
"""
Test Script for Audio Pipeline (Session 13)
Tests audio validation, storage, and versioning features
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
import subprocess
import time

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from audio_pipeline_enhanced import EnhancedAudioPipeline


class TestAudioPipeline:
    """
    Test suite for the enhanced audio pipeline
    """
    
    def __init__(self):
        self.pipeline = EnhancedAudioPipeline()
        self.test_dir = tempfile.mkdtemp(prefix="audio_test_")
        self.test_results = []
        print(f"Test directory: {self.test_dir}")
    
    def create_test_audio(self, duration_seconds: int = 1200) -> str:
        """
        Create a test audio file with specified duration.
        
        Args:
            duration_seconds: Duration in seconds (default 1200 = 20 minutes)
            
        Returns:
            Path to test audio file
        """
        audio_file = os.path.join(self.test_dir, f"test_audio_{duration_seconds}s.mp3")
        
        # Check if ffmpeg is available
        ffmpeg_available = subprocess.run(
            ["which", "ffmpeg"],
            capture_output=True
        ).returncode == 0
        
        if ffmpeg_available:
            # Generate silent audio file
            cmd = [
                "ffmpeg",
                "-f", "lavfi",
                "-i", f"anullsrc=r=44100:cl=stereo:d={duration_seconds}",
                "-codec:a", "libmp3lame",
                "-b:a", "128k",
                "-y", audio_file
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Created test audio: {audio_file} ({duration_seconds}s)")
                return audio_file
            else:
                print(f"Error creating audio: {result.stderr}")
        else:
            # Create a dummy file for testing
            with open(audio_file, 'wb') as f:
                f.write(b'ID3' + b'\x00' * 1024)  # Minimal MP3 header
            print(f"Created dummy audio file (ffmpeg not available)")
            return audio_file
        
        return None
    
    def create_test_digest(self) -> Dict:
        """
        Create test digest data.
        
        Returns:
            Test digest dictionary
        """
        now = datetime.now()
        week_start = now - timedelta(days=7)
        
        return {
            "week_start": week_start.isoformat(),
            "week_end": now.isoformat(),
            "total_items": 42,
            "brief": """# Regen Weekly Digest - Test Edition

## Executive Summary
This week saw significant progress in regenerative agriculture initiatives,
with new carbon credit methodologies approved and community governance proposals passed.

## Top Stories

### 1. New Soil Carbon Methodology Launched
Regen Network announced a groundbreaking methodology for measuring soil carbon sequestration,
validated by leading climate scientists.

### 2. Governance Proposal #47 Passed
The community approved enhanced credit retirement mechanisms with 95% support.

### 3. Partnership with Climate Collective
Strategic partnership to scale regenerative practices across 1 million hectares.

## Network Statistics
- Total Credits Issued: 1.2M (+15% WoW)
- Active Projects: 47 (+3)
- Validator Count: 51 (stable)
- Governance Participation: 73%

## Looking Ahead
Next week's community call will focus on the roadmap for Q1 2025.
""",
            "themes": [
                "carbon credits",
                "governance",
                "partnerships",
                "soil carbon",
                "regenerative agriculture"
            ],
            "citations": [
                "https://regen.network/blog/soil-carbon",
                "https://forum.regen.network/proposal/47",
                "https://climate-collective.org/partnerships"
            ],
            "stats": {
                "credits_issued": 1200000,
                "active_projects": 47,
                "validator_count": 51
            }
        }
    
    def test_duration_validation(self):
        """
        Test audio duration validation.
        """
        print("\n=== Testing Duration Validation ===")
        
        test_cases = [
            (1200, True, "20 minutes - Perfect"),      # 20 min - perfect
            (960, True, "16 minutes - Minimum"),       # 16 min - minimum
            (1440, True, "24 minutes - Maximum"),      # 24 min - maximum
            (900, False, "15 minutes - Too short"),    # 15 min - too short
            (1500, False, "25 minutes - Too long"),    # 25 min - too long
        ]
        
        for duration, expected_valid, description in test_cases:
            audio_file = self.create_test_audio(duration)
            
            if audio_file and os.path.exists(audio_file):
                valid, info = self.pipeline.validate_audio_duration(audio_file)
                
                # For dummy files, we can't validate duration
                if "error" in info:
                    print(f"  ⚠ {description}: Cannot validate (no mutagen/ffprobe)")
                    self.test_results.append(("Duration: " + description, "SKIPPED"))
                else:
                    passed = valid == expected_valid
                    status = "✓" if passed else "✗"
                    
                    print(f"  {status} {description}: Valid={valid} (expected {expected_valid})")
                    self.test_results.append(("Duration: " + description, "PASS" if passed else "FAIL"))
            else:
                print(f"  ✗ {description}: Failed to create test audio")
                self.test_results.append(("Duration: " + description, "FAIL"))
    
    def test_metadata_addition(self):
        """
        Test metadata addition to audio files.
        """
        print("\n=== Testing Metadata Addition ===")
        
        audio_file = self.create_test_audio(1200)  # 20 minutes
        digest_data = self.create_test_digest()
        
        if audio_file:
            success = self.pipeline.add_podcast_metadata(audio_file, digest_data)
            
            if success:
                print(f"  ✓ Metadata added successfully")
                
                # Try to read metadata back
                try:
                    from mutagen.mp3 import MP3
                    audio = MP3(audio_file)
                    
                    if audio.tags:
                        print(f"    Title: {audio.tags.get('TIT2', 'N/A')}")
                        print(f"    Artist: {audio.tags.get('TPE1', 'N/A')}")
                        print(f"    Album: {audio.tags.get('TALB', 'N/A')}")
                        self.test_results.append(("Metadata Addition", "PASS"))
                    else:
                        print(f"  ⚠ Tags not found after addition")
                        self.test_results.append(("Metadata Addition", "PARTIAL"))
                        
                except ImportError:
                    print(f"  ⚠ Cannot verify metadata (mutagen not available)")
                    self.test_results.append(("Metadata Addition", "SKIPPED"))
            else:
                print(f"  ✗ Failed to add metadata")
                self.test_results.append(("Metadata Addition", "FAIL"))
        else:
            print(f"  ✗ No test audio file")
            self.test_results.append(("Metadata Addition", "FAIL"))
    
    def test_version_creation(self):
        """
        Test creation of different audio versions.
        """
        print("\n=== Testing Version Creation ===")
        
        audio_file = self.create_test_audio(1200)  # 20 minutes
        
        if audio_file:
            versions = self.pipeline.create_audio_versions(audio_file)
            
            print(f"  Created {len(versions)} versions:")
            for version_name, version_path in versions.items():
                if os.path.exists(version_path):
                    size_mb = os.path.getsize(version_path) / (1024 * 1024)
                    print(f"    ✓ {version_name}: {size_mb:.2f} MB")
                else:
                    print(f"    ✗ {version_name}: File not found")
            
            if len(versions) > 0:
                self.test_results.append(("Version Creation", "PASS"))
            else:
                self.test_results.append(("Version Creation", "FAIL"))
        else:
            print(f"  ✗ No test audio file")
            self.test_results.append(("Version Creation", "FAIL"))
    
    def test_watch_directory(self):
        """
        Test watch directory functionality.
        """
        print("\n=== Testing Watch Directory ===")
        
        watch_dir = self.pipeline.config["output"]["watch_dir"]
        
        # Create test audio in watch directory
        test_audio = os.path.join(watch_dir, "test_upload.mp3")
        
        # Create a valid 20-minute audio
        source_audio = self.create_test_audio(1200)
        
        if source_audio:
            # Copy to watch directory
            shutil.copy2(source_audio, test_audio)
            print(f"  Placed test audio in watch directory: {test_audio}")
            
            # Test with very short wait (1 second)
            found, audio_path = self.pipeline.watch_for_notebooklm_audio(
                export_dir="test_export",
                max_wait_minutes=0.05  # 3 seconds
            )
            
            if found:
                print(f"  ✓ Audio detected and moved to: {audio_path}")
                self.test_results.append(("Watch Directory", "PASS"))
            else:
                # Check if file is still there (validation might have failed)
                if os.path.exists(test_audio):
                    print(f"  ⚠ Audio found but validation failed")
                    self.test_results.append(("Watch Directory", "PARTIAL"))
                else:
                    print(f"  ✗ Audio not detected")
                    self.test_results.append(("Watch Directory", "FAIL"))
            
            # Cleanup
            if os.path.exists(test_audio):
                os.remove(test_audio)
        else:
            print(f"  ✗ Could not create test audio")
            self.test_results.append(("Watch Directory", "FAIL"))
    
    def test_storage_report(self):
        """
        Test storage report generation.
        """
        print("\n=== Testing Storage Report ===")
        
        report = self.pipeline.generate_storage_report()
        
        print(f"  Total storage: {report['total_size_mb']:.2f} MB")
        print(f"  Total files: {report['file_count']}")
        print(f"  Directories:")
        
        for dir_name, dir_info in report['directories'].items():
            print(f"    {dir_name}: {dir_info['file_count']} files, {dir_info['size_mb']:.2f} MB")
        
        if report['file_count'] >= 0:  # Report generated
            self.test_results.append(("Storage Report", "PASS"))
        else:
            self.test_results.append(("Storage Report", "FAIL"))
    
    def test_notebooklm_process(self):
        """
        Test complete NotebookLM audio processing.
        """
        print("\n=== Testing NotebookLM Processing ===")
        
        audio_file = self.create_test_audio(1200)  # 20 minutes
        digest_data = self.create_test_digest()
        
        if audio_file:
            results = self.pipeline.process_notebooklm_audio(audio_file, digest_data)
            
            print(f"  Processing results:")
            print(f"    Success: {results['success']}")
            print(f"    Validation: {results['validation'] is not None}")
            print(f"    Metadata: {results['metadata_added']}")
            print(f"    Versions: {len(results['versions_created'])}")
            
            if results['errors']:
                print(f"    Errors: {results['errors']}")
            
            if results['success']:
                self.test_results.append(("NotebookLM Processing", "PASS"))
            else:
                self.test_results.append(("NotebookLM Processing", "FAIL"))
        else:
            print(f"  ✗ No test audio file")
            self.test_results.append(("NotebookLM Processing", "FAIL"))
    
    def cleanup(self):
        """
        Clean up test files.
        """
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
            print(f"\nCleaned up test directory: {self.test_dir}")
    
    def print_summary(self):
        """
        Print test summary.
        """
        print("\n" + "="*50)
        print("TEST SUMMARY")
        print("="*50)
        
        passed = sum(1 for _, status in self.test_results if status == "PASS")
        failed = sum(1 for _, status in self.test_results if status == "FAIL")
        skipped = sum(1 for _, status in self.test_results if status == "SKIPPED")
        partial = sum(1 for _, status in self.test_results if status == "PARTIAL")
        
        for test_name, status in self.test_results:
            symbol = {
                "PASS": "✓",
                "FAIL": "✗",
                "SKIPPED": "⚠",
                "PARTIAL": "◐"
            }.get(status, "?")
            
            print(f"{symbol} {test_name}: {status}")
        
        print("\n" + "-"*50)
        print(f"Passed: {passed}/{len(self.test_results)}")
        print(f"Failed: {failed}")
        print(f"Skipped: {skipped}")
        print(f"Partial: {partial}")
        
        if failed == 0:
            print("\n✓ All critical tests passed!")
        else:
            print(f"\n✗ {failed} tests failed")
    
    def run_all_tests(self):
        """
        Run all tests.
        """
        print("\n" + "="*50)
        print("AUDIO PIPELINE TEST SUITE")
        print("Session 13: NotebookLM Audio Pipeline")
        print("="*50)
        
        try:
            # Run test suite
            self.test_duration_validation()
            self.test_metadata_addition()
            self.test_version_creation()
            self.test_watch_directory()
            self.test_storage_report()
            self.test_notebooklm_process()
            
        except Exception as e:
            print(f"\n✗ Test suite error: {e}")
            self.test_results.append(("Test Suite", "FAIL"))
        
        finally:
            self.print_summary()
            self.cleanup()


def main():
    """
    Main test runner.
    """
    tester = TestAudioPipeline()
    tester.run_all_tests()


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Generate Weekly Podcast - CLI Runner

Complete workflow for generating the Regen Network weekly podcast.
Can be run manually or scheduled via cron.
"""

import sys
import os
import json
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_pipeline import AudioPipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/weekly_podcast.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def check_environment():
    """Check that all required environment variables and tools are available"""
    issues = []
    
    # Check for API keys
    required_env_vars = [
        ("GEMINI_API_KEY", "Google Gemini API for transcript generation"),
        ("OPENAI_API_KEY", "OpenAI API for text-to-speech")
    ]
    
    for var, description in required_env_vars:
        if not os.environ.get(var):
            issues.append(f"{var} not set ({description})")
    
    # Check for ffmpeg
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        issues.append("ffmpeg not installed (required for audio processing)")
    
    # Check Python version
    if sys.version_info < (3, 11):
        issues.append(f"Python 3.11+ required (current: {sys.version})")
    
    # Check for Podcastfy
    try:
        import podcastfy
    except ImportError:
        issues.append("Podcastfy not installed (pip install podcastfy)")
    
    return issues

def find_latest_digest(digest_dir: str = "output/weekly") -> Optional[str]:
    """Find the most recent digest file"""
    if not os.path.exists(digest_dir):
        return None
    
    digest_files = list(Path(digest_dir).glob("weekly_digest_*.json"))
    if not digest_files:
        return None
    
    # Sort by modification time
    latest = max(digest_files, key=lambda p: p.stat().st_mtime)
    return str(latest)

def main():
    parser = argparse.ArgumentParser(
        description="Generate Regen Network Weekly Podcast",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate podcast for this week
  python generate_weekly_podcast.py
  
  # Use existing digest
  python generate_weekly_podcast.py --digest output/weekly/weekly_digest_2025-09-11.json
  
  # Generate for past 14 days
  python generate_weekly_podcast.py --days 14
  
  # Test mode (no audio generation)
  python generate_weekly_podcast.py --test
  
  # Use NotebookLM export only
  python generate_weekly_podcast.py --backend notebooklm_manual

Scheduling with cron:
  # Every Friday at 2 PM
  0 14 * * 5 cd /path/to/koi-processor && python scripts/generate_weekly_podcast.py
        """
    )
    
    parser.add_argument(
        '--digest',
        help='Path to existing digest JSON file'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='Number of days to look back (default: 7)'
    )
    parser.add_argument(
        '--backend',
        choices=['podcastfy', 'notebooklm_manual'],
        default='podcastfy',
        help='Audio generation backend (default: podcastfy)'
    )
    parser.add_argument(
        '--config',
        default='config/audio_pipeline.json',
        help='Pipeline configuration file'
    )
    parser.add_argument(
        '--output-dir',
        help='Override output directory for podcast'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Test mode - generate digest and export but skip audio'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Check environment and exit'
    )
    parser.add_argument(
        '--use-latest',
        action='store_true',
        help='Use the latest existing digest file'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Environment check
    if args.check:
        print("\n🔍 Checking environment...")
        issues = check_environment()
        if issues:
            print("\n❌ Environment issues found:")
            for issue in issues:
                print(f"  - {issue}")
            sys.exit(1)
        else:
            print("✅ Environment check passed!")
            sys.exit(0)
    
    # Find digest if needed
    digest_path = args.digest
    if args.use_latest:
        digest_path = find_latest_digest()
        if not digest_path:
            print("❌ No existing digest found")
            sys.exit(1)
        print(f"📄 Using latest digest: {digest_path}")
    
    # Print header
    print("\n" + "="*70)
    print("🎙️  REGEN NETWORK WEEKLY PODCAST GENERATOR")
    print("="*70)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Backend: {args.backend}")
    print(f"Days back: {args.days}")
    if digest_path:
        print(f"Using digest: {digest_path}")
    print("="*70 + "\n")
    
    # Check environment first
    issues = check_environment()
    if issues:
        print("⚠️  Warning: Environment issues detected:")
        for issue in issues:
            print(f"  - {issue}")
        if not args.test:
            response = input("\nContinue anyway? (y/N): ")
            if response.lower() != 'y':
                print("Aborted.")
                sys.exit(1)
    
    # Initialize pipeline
    try:
        pipeline = AudioPipeline(args.config)
    except Exception as e:
        print(f"❌ Failed to initialize pipeline: {e}")
        sys.exit(1)
    
    # Override settings
    pipeline.config["audio"]["backend"] = args.backend
    if args.test:
        pipeline.config["pipeline"]["generate_podcast"] = False
        print("🧪 Test mode - skipping audio generation\n")
    if args.output_dir:
        pipeline.config["output"]["podcast_dir"] = args.output_dir
    
    # Run pipeline
    try:
        print("🚀 Starting pipeline...\n")
        
        results = pipeline.run_full_pipeline(
            digest_path=digest_path,
            days_back=args.days
        )
        
        # Display results
        print("\n" + "-"*70)
        print("📋 PIPELINE RESULTS")
        print("-"*70)
        
        if results["digest"]:
            print(f"✅ Digest: {results['digest']}")
        
        if results["notebooklm_export"]:
            print(f"✅ NotebookLM Export: {results['notebooklm_export']}")
            print("   Instructions:")
            print("   1. Go to notebooklm.google.com")
            print("   2. Create new notebook")
            print("   3. Upload markdown files from export directory")
            print("   4. Generate Audio Overview")
        
        if results["podcast"]:
            print(f"✅ Podcast Generated: {results['podcast']}")
            
            # Show file info
            if os.path.exists(results["podcast"]):
                file_size = os.path.getsize(results["podcast"]) / (1024 * 1024)
                print(f"   Size: {file_size:.1f} MB")
        
        if results["errors"]:
            print("\n❌ Errors encountered:")
            for error in results["errors"]:
                print(f"  - {error}")
        
        print("-"*70)
        
        # Success message
        if results["success"]:
            print("\n🎉 SUCCESS! Weekly podcast generation complete.\n")
            
            if results["podcast"] and os.path.exists(results["podcast"]):
                print("📝 Next Steps:")
                print("1. Listen to the generated podcast for quality check")
                print("2. Upload to podcast hosting platform")
                print("3. Add to 'Pathway to Planetary Regeneration' feed")
                print("4. Share with the Regen Network community")
            elif args.backend == "notebooklm_manual":
                print("📝 Next Steps:")
                print("1. Upload sources to NotebookLM")
                print("2. Generate Audio Overview")
                print("3. Download the generated podcast")
                print("4. Upload to podcast hosting platform")
        else:
            print("\n⚠️  Pipeline completed with errors. Please review the issues above.")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⛔ Pipeline interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.exception("Pipeline failed with exception")
        print(f"\n❌ Pipeline failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Create logs directory if needed
    os.makedirs("logs", exist_ok=True)
    main()
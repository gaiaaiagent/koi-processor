#!/usr/bin/env python3
"""
Unified Audio Pipeline for Regen Network Weekly Digest

Orchestrates the complete audio generation workflow from digest to podcast.
Supports multiple backends: Podcastfy (primary) and NotebookLM (export only).
"""

import json
import os
import sys
import logging
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import shutil

# Import our modules
from weekly_aggregator import WeeklyAggregator
from notebooklm_exporter import NotebookLMExporter
from podcastfy_generator import PodcastfyGenerator, PODCASTFY_AVAILABLE

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class AudioPipeline:
    """
    Unified pipeline for generating audio content from weekly digests.
    
    Workflow:
    1. Generate or load weekly digest
    2. Export to NotebookLM format (optional)
    3. Generate podcast using Podcastfy
    4. Post-process and distribute
    """
    
    def __init__(self, config_path: str = "config/audio_pipeline.json"):
        """Initialize the audio pipeline"""
        self.config = self._load_config(config_path)
        self.weekly_aggregator = None
        self.notebooklm_exporter = NotebookLMExporter()
        self.podcastfy_generator = None
        
        if PODCASTFY_AVAILABLE:
            self.podcastfy_generator = PodcastfyGenerator()
    
    def _load_config(self, config_path: str) -> Dict:
        """Load pipeline configuration"""
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return json.load(f)
        else:
            # Default configuration
            return {
                "pipeline": {
                    "generate_digest": True,
                    "export_notebooklm": True,
                    "generate_podcast": True,
                    "archive_outputs": True
                },
                "digest": {
                    "days_back": 7,
                    "config_file": "config/weekly_aggregator.json"
                },
                "audio": {
                    "backend": "podcastfy",  # or "notebooklm_manual"
                    "config_file": "config/audio_generation.json",
                    "target_duration_minutes": 20
                },
                "output": {
                    "digest_dir": "output/weekly",
                    "notebooklm_dir": "output/notebooklm",
                    "podcast_dir": "output/podcasts",
                    "archive_dir": "output/archive"
                },
                "distribution": {
                    "podcast_feed_dir": "/var/www/podcasts",
                    "enable_auto_publish": False
                }
            }
    
    def generate_digest(self, days_back: Optional[int] = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Generate or load the weekly digest.
        
        Returns:
            Tuple of (success, digest_data or error_message)
        """
        try:
            if not self.weekly_aggregator:
                digest_config = self.config["digest"]["config_file"]
                self.weekly_aggregator = WeeklyAggregator(digest_config)
            
            days = days_back or self.config["digest"]["days_back"]
            logger.info(f"Generating digest for past {days} days...")
            
            digest = self.weekly_aggregator.generate_digest(days)
            
            if digest:
                # Convert to dict for JSON serialization
                digest_data = {
                    "week_start": digest.week_start.isoformat(),
                    "week_end": digest.week_end.isoformat(),
                    "total_items": digest.total_items,
                    "clusters": digest.clusters,
                    "top_stories": [
                        {
                            "id": story.id,
                            "title": story.title,
                            "content": story.content,
                            "source": story.source,
                            "url": story.url,
                            "publication_date": story.publication_date.isoformat(),
                            "confidence": story.confidence,
                            "tags": story.tags,
                            "relevance_score": story.relevance_score
                        }
                        for story in digest.top_stories
                    ],
                    "brief": digest.brief,
                    "citations": digest.citations,
                    "stats": digest.stats
                }
                
                # Save digest
                output_dir = self.config["output"]["digest_dir"]
                os.makedirs(output_dir, exist_ok=True)
                
                date_str = digest.week_end.strftime('%Y-%m-%d')
                json_path = os.path.join(output_dir, f"weekly_digest_{date_str}.json")
                
                with open(json_path, 'w') as f:
                    json.dump(digest_data, f, indent=2)
                
                logger.info(f"Digest saved to {json_path}")
                return True, digest_data
            else:
                return False, "Failed to generate digest"
                
        except Exception as e:
            logger.error(f"Error generating digest: {e}")
            return False, str(e)
    
    def export_to_notebooklm(self, digest_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Export digest to NotebookLM format.
        
        Returns:
            Tuple of (success, output_dir or error_message)
        """
        try:
            logger.info("Exporting to NotebookLM format...")
            
            # Format for NotebookLM
            sources = self.notebooklm_exporter.format_for_notebooklm(digest_data)
            
            # Save to files
            output_dir = self.config["output"]["notebooklm_dir"]
            date_str = datetime.now().strftime('%Y-%m-%d')
            export_dir = os.path.join(output_dir, date_str)
            
            manifest_path = self.notebooklm_exporter.export_to_files(sources, export_dir)
            
            logger.info(f"NotebookLM export complete: {export_dir}")
            logger.info(f"  Sources: {len(sources)}")
            logger.info(f"  Manifest: {manifest_path}")
            
            return True, export_dir
            
        except Exception as e:
            logger.error(f"Error exporting to NotebookLM: {e}")
            return False, str(e)
    
    def generate_podcast(self, digest_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Generate podcast audio from digest.
        
        Returns:
            Tuple of (success, audio_file_path or error_message)
        """
        backend = self.config["audio"]["backend"]
        
        if backend == "podcastfy":
            if not self.podcastfy_generator:
                return False, "Podcastfy not available"
            
            logger.info("Generating podcast with Podcastfy...")
            
            # Generate output path
            date_str = datetime.now().strftime('%Y-%m-%d')
            output_dir = self.config["output"]["podcast_dir"]
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"regen_weekly_{date_str}.mp3")
            
            # Generate podcast
            success, result = self.podcastfy_generator.generate_podcast(
                digest_data,
                output_path=output_path
            )
            
            return success, result
            
        elif backend == "notebooklm_manual":
            # Export for manual NotebookLM processing
            success, export_dir = self.export_to_notebooklm(digest_data)
            if success:
                return True, f"NotebookLM sources exported to {export_dir}. Manual audio generation required."
            else:
                return False, export_dir
        
        else:
            return False, f"Unknown audio backend: {backend}"
    
    def archive_outputs(self, digest_data: Dict[str, Any], audio_file: Optional[str] = None):
        """
        Archive all outputs for the week.
        """
        try:
            archive_dir = self.config["output"]["archive_dir"]
            date_str = datetime.now().strftime('%Y-%m-%d')
            week_archive = os.path.join(archive_dir, date_str)
            os.makedirs(week_archive, exist_ok=True)
            
            # Save digest
            digest_path = os.path.join(week_archive, "digest.json")
            with open(digest_path, 'w') as f:
                json.dump(digest_data, f, indent=2)
            
            # Save brief
            brief_path = os.path.join(week_archive, "brief.md")
            with open(brief_path, 'w') as f:
                f.write(digest_data.get("brief", ""))
            
            # Copy audio if exists
            if audio_file and os.path.exists(audio_file):
                audio_dest = os.path.join(week_archive, os.path.basename(audio_file))
                shutil.copy2(audio_file, audio_dest)
            
            # Create metadata
            metadata = {
                "generated_at": datetime.now().isoformat(),
                "week_start": digest_data.get("week_start"),
                "week_end": digest_data.get("week_end"),
                "total_items": digest_data.get("total_items"),
                "audio_file": os.path.basename(audio_file) if audio_file else None,
                "pipeline_version": "1.0.0"
            }
            
            metadata_path = os.path.join(week_archive, "metadata.json")
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            logger.info(f"Archived outputs to {week_archive}")
            
        except Exception as e:
            logger.error(f"Error archiving outputs: {e}")
    
    def run_full_pipeline(
        self,
        digest_path: Optional[str] = None,
        days_back: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Run the complete audio generation pipeline.
        
        Args:
            digest_path: Optional path to existing digest JSON
            days_back: Optional number of days to look back
        
        Returns:
            Dictionary with pipeline results
        """
        results = {
            "success": False,
            "digest": None,
            "notebooklm_export": None,
            "podcast": None,
            "errors": []
        }
        
        try:
            # Step 1: Get digest
            if digest_path and os.path.exists(digest_path):
                logger.info(f"Loading existing digest from {digest_path}")
                with open(digest_path, 'r') as f:
                    digest_data = json.load(f)
                results["digest"] = digest_path
            else:
                if self.config["pipeline"]["generate_digest"]:
                    success, result = self.generate_digest(days_back)
                    if success:
                        digest_data = result
                        results["digest"] = "Generated successfully"
                    else:
                        results["errors"].append(f"Digest generation failed: {result}")
                        return results
                else:
                    results["errors"].append("No digest provided and generation disabled")
                    return results
            
            # Step 2: Export to NotebookLM (optional)
            if self.config["pipeline"]["export_notebooklm"]:
                success, result = self.export_to_notebooklm(digest_data)
                if success:
                    results["notebooklm_export"] = result
                else:
                    results["errors"].append(f"NotebookLM export failed: {result}")
            
            # Step 3: Generate podcast
            if self.config["pipeline"]["generate_podcast"]:
                success, result = self.generate_podcast(digest_data)
                if success:
                    results["podcast"] = result
                else:
                    results["errors"].append(f"Podcast generation failed: {result}")
            
            # Step 4: Archive outputs
            if self.config["pipeline"]["archive_outputs"]:
                audio_file = results.get("podcast") if results.get("podcast") and os.path.exists(results["podcast"]) else None
                self.archive_outputs(digest_data, audio_file)
            
            # Determine overall success
            results["success"] = len(results["errors"]) == 0
            
            return results
            
        except Exception as e:
            results["errors"].append(f"Pipeline error: {str(e)}")
            return results

def main():
    """CLI entry point for audio pipeline"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Run the Regen Network weekly audio generation pipeline"
    )
    parser.add_argument(
        '--digest',
        help='Path to existing digest JSON (skip generation)'
    )
    parser.add_argument(
        '--days',
        type=int,
        help='Number of days to look back for digest generation'
    )
    parser.add_argument(
        '--config',
        default='config/audio_pipeline.json',
        help='Pipeline configuration file'
    )
    parser.add_argument(
        '--backend',
        choices=['podcastfy', 'notebooklm_manual'],
        help='Override audio generation backend'
    )
    parser.add_argument(
        '--skip-notebooklm',
        action='store_true',
        help='Skip NotebookLM export'
    )
    parser.add_argument(
        '--skip-podcast',
        action='store_true',
        help='Skip podcast generation'
    )
    
    args = parser.parse_args()
    
    # Initialize pipeline
    pipeline = AudioPipeline(args.config)
    
    # Override config if needed
    if args.backend:
        pipeline.config["audio"]["backend"] = args.backend
    if args.skip_notebooklm:
        pipeline.config["pipeline"]["export_notebooklm"] = False
    if args.skip_podcast:
        pipeline.config["pipeline"]["generate_podcast"] = False
    
    # Run pipeline
    print("\n🚀 Starting Regen Network Audio Pipeline")
    print("="*60)
    
    results = pipeline.run_full_pipeline(
        digest_path=args.digest,
        days_back=args.days
    )
    
    # Display results
    print("\n" + "="*60)
    print("📋 Pipeline Results:")
    print("="*60)
    
    if results["digest"]:
        print(f"✅ Digest: {results['digest']}")
    
    if results["notebooklm_export"]:
        print(f"✅ NotebookLM Export: {results['notebooklm_export']}")
    
    if results["podcast"]:
        print(f"✅ Podcast: {results['podcast']}")
    
    if results["errors"]:
        print("\n❌ Errors:")
        for error in results["errors"]:
            print(f"  - {error}")
    
    if results["success"]:
        print("\n🎉 Pipeline completed successfully!")
        
        if results["podcast"] and os.path.exists(results["podcast"]):
            print("\n📝 Next Steps:")
            print("1. Review the generated audio")
            print("2. Upload to podcast hosting platform")
            print("3. Add to Pathway to Planetary Regeneration feed")
    else:
        print("\n⚠️ Pipeline completed with errors")
        sys.exit(1)

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Podcastfy Generator for Regen Network Weekly Digest

Generates conversational podcast audio from weekly digest using Podcastfy,
an open-source alternative to NotebookLM's Audio Overview feature.
"""

import json
import os
import sys
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import tempfile

try:
    from podcastfy.client import generate_podcast
    PODCASTFY_AVAILABLE = True
except ImportError:
    PODCASTFY_AVAILABLE = False
    print("Warning: Podcastfy not installed. Install with: pip install podcastfy")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PodcastfyGenerator:
    """Generate podcasts from weekly digests using Podcastfy"""
    
    def __init__(self, config_path: str = "config/audio_generation.json"):
        """Initialize the podcast generator"""
        self.config = self._load_config(config_path)
        self.validate_environment()
    
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from JSON file"""
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return json.load(f)
        else:
            # Default configuration
            return {
                "podcastfy": {
                    "llm_model": "gemini-1.5-pro-latest",
                    "api_key_label": "GEMINI_API_KEY",
                    "tts_provider": "openai",
                    "tts_model": "tts-1",
                    "podcast_length": "medium",  # short, medium, or long
                    "conversation_style": "informative",
                    "creativity": 0.7,
                    "language": "English",
                    "output_format": "mp3"
                },
                "content": {
                    "max_topics": 10,
                    "max_stories_per_topic": 3,
                    "include_stats": True,
                    "include_citations": False  # Too verbose for audio
                },
                "audio": {
                    "target_duration_minutes": 20,
                    "intro_style": "welcoming",
                    "outro_style": "summary"
                }
            }
    
    def validate_environment(self):
        """Validate that required environment variables and tools are available"""
        issues = []
        
        # Check Podcastfy
        if not PODCASTFY_AVAILABLE:
            issues.append("Podcastfy not installed. Run: pip install podcastfy")
        
        # Check for API keys based on config
        api_key_label = self.config["podcastfy"].get("api_key_label", "GEMINI_API_KEY")
        if not os.environ.get(api_key_label):
            issues.append(f"Environment variable {api_key_label} not set")
        
        # Check for TTS API key if using OpenAI
        if self.config["podcastfy"].get("tts_provider") == "openai":
            if not os.environ.get("OPENAI_API_KEY"):
                issues.append("OPENAI_API_KEY not set for TTS")
        
        # Check ffmpeg
        import subprocess
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            issues.append("ffmpeg not installed. Required for audio processing.")
        
        if issues:
            logger.warning("Environment validation issues:")
            for issue in issues:
                logger.warning(f"  - {issue}")
        else:
            logger.info("Environment validation successful")
    
    def prepare_content_for_podcast(self, digest_data: Dict[str, Any]) -> List[str]:
        """
        Prepare digest content as input sources for Podcastfy.
        
        Returns a list of text content chunks optimized for conversation generation.
        """
        content_sources = []
        
        # 1. Executive Summary
        exec_summary = self._create_executive_summary(digest_data)
        if exec_summary:
            content_sources.append(exec_summary)
        
        # 2. Top Stories
        top_stories = self._format_top_stories(digest_data.get("top_stories", []))
        if top_stories:
            content_sources.append(top_stories)
        
        # 3. Thematic Clusters
        themes = self._format_themes(digest_data.get("clusters", []))
        if themes:
            content_sources.append(themes)
        
        # 4. Statistics (if enabled)
        if self.config["content"].get("include_stats"):
            stats = self._format_statistics(digest_data.get("stats", {}))
            if stats:
                content_sources.append(stats)
        
        return content_sources
    
    def _create_executive_summary(self, digest_data: Dict[str, Any]) -> str:
        """Create executive summary optimized for audio"""
        lines = []
        
        lines.append("REGEN NETWORK WEEKLY DIGEST EXECUTIVE SUMMARY\n")
        
        week_start = digest_data.get("week_start", "")
        week_end = digest_data.get("week_end", "")
        if week_start and week_end:
            # Parse dates and format them nicely
            try:
                start = datetime.fromisoformat(week_start.replace('Z', '+00:00'))
                end = datetime.fromisoformat(week_end.replace('Z', '+00:00'))
                lines.append(f"Period: {start.strftime('%B %d')} to {end.strftime('%B %d, %Y')}\n")
            except:
                lines.append(f"Period: {week_start} to {week_end}\n")
        
        total_items = digest_data.get("total_items", 0)
        lines.append(f"This week saw {total_items} significant updates across the Regen Network ecosystem.\n")
        
        # Add key themes
        clusters = digest_data.get("clusters", [])
        if clusters:
            top_themes = [c.get('theme', 'Updates') for c in clusters[:3]]
            lines.append(f"The main focus areas were: {', '.join(top_themes)}.\n")
        
        # Add context
        lines.append("\nThe Regen Network continues advancing planetary regeneration through ")
        lines.append("coordinated action in technology, governance, and community engagement. ")
        lines.append("This week's developments show growing momentum in regenerative finance ")
        lines.append("and ecological data infrastructure.\n")
        
        return '\n'.join(lines)
    
    def _format_top_stories(self, stories: List[Dict[str, Any]]) -> str:
        """Format top stories for conversational audio"""
        if not stories:
            return ""
        
        lines = ["TOP STORIES THIS WEEK\n"]
        
        max_stories = min(len(stories), self.config["content"].get("max_topics", 10))
        
        for i, story in enumerate(stories[:max_stories], 1):
            lines.append(f"\nStory {i}: {story.get('title', 'Update')}")
            lines.append(f"Source: {story.get('source', 'Regen Network')}")
            
            # Add publication date if available
            pub_date = story.get('publication_date')
            if pub_date:
                try:
                    date = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                    lines.append(f"Date: {date.strftime('%B %d, %Y')}")
                except:
                    pass
            
            # Add concise content
            content = story.get('content', '')
            if content:
                # Truncate for audio - shorter is better
                max_chars = 200
                if len(content) > max_chars:
                    content = content[:max_chars].rsplit(' ', 1)[0] + "..."
                lines.append(f"Summary: {content}")
            
            # Add tags if relevant
            tags = story.get('tags', [])
            if tags:
                lines.append(f"Topics: {', '.join(tags[:3])}")
        
        return '\n'.join(lines)
    
    def _format_themes(self, clusters: List[Dict[str, Any]]) -> str:
        """Format thematic clusters for audio discussion"""
        if not clusters:
            return ""
        
        lines = ["THEMATIC ANALYSIS\n"]
        lines.append("This week's content clustered around several key themes:\n")
        
        max_themes = min(len(clusters), 5)
        
        for cluster in clusters[:max_themes]:
            theme = cluster.get('theme', 'General')
            size = cluster.get('size', 0)
            
            lines.append(f"\nTheme: {theme}")
            lines.append(f"Number of related items: {size}")
            
            # Add sample items
            items = cluster.get('items', [])
            if items:
                lines.append("Key developments:")
                for item in items[:3]:
                    lines.append(f"- {item.get('title', 'Item')}")
        
        return '\n'.join(lines)
    
    def _format_statistics(self, stats: Dict[str, Any]) -> str:
        """Format statistics for audio presentation"""
        if not stats:
            return ""
        
        lines = ["WEEKLY METRICS\n"]
        
        # Key metrics
        total = stats.get('total_items', 0)
        sources = stats.get('unique_sources', 0)
        top_source = stats.get('most_active_source', 'Unknown')
        
        lines.append(f"Total content items analyzed: {total}")
        lines.append(f"Number of unique sources: {sources}")
        lines.append(f"Most active source: {top_source}")
        
        # Confidence metrics
        avg_conf = stats.get('avg_confidence', 0)
        if avg_conf:
            lines.append(f"Average confidence score: {avg_conf:.1%}")
        
        return '\n'.join(lines)
    
    def generate_podcast(
        self, 
        digest_data: Dict[str, Any],
        output_path: Optional[str] = None,
        custom_config: Optional[Dict] = None
    ) -> Tuple[bool, str]:
        """
        Generate a podcast from weekly digest data.
        
        Args:
            digest_data: Weekly digest JSON data
            output_path: Optional output file path
            custom_config: Optional config overrides
        
        Returns:
            Tuple of (success, output_path or error_message)
        """
        if not PODCASTFY_AVAILABLE:
            return False, "Podcastfy not installed"
        
        try:
            # Merge custom config if provided
            config = self.config.copy()
            if custom_config:
                config.update(custom_config)
            
            # Prepare content
            logger.info("Preparing content for podcast generation...")
            content_sources = self.prepare_content_for_podcast(digest_data)
            
            if not content_sources:
                return False, "No content to generate podcast from"
            
            # Create temporary files for content
            temp_files = []
            temp_dir = tempfile.mkdtemp(prefix="podcastfy_")
            
            for i, content in enumerate(content_sources):
                temp_file = os.path.join(temp_dir, f"source_{i}.txt")
                with open(temp_file, 'w') as f:
                    f.write(content)
                temp_files.append(temp_file)
            
            logger.info(f"Created {len(temp_files)} source files for podcast generation")
            
            # Generate output path if not provided
            if not output_path:
                date_str = datetime.now().strftime('%Y-%m-%d')
                output_path = f"output/podcasts/regen_weekly_{date_str}.mp3"
            
            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Configure Podcastfy parameters
            podcast_config = config.get("podcastfy", {})
            
            logger.info("Generating podcast with Podcastfy...")
            logger.info(f"Config: Model={podcast_config.get('llm_model')}, " 
                       f"TTS={podcast_config.get('tts_provider')}, "
                       f"Length={podcast_config.get('podcast_length')}")
            
            # Generate podcast
            # Note: Podcastfy expects file paths or URLs
            audio_file = generate_podcast(
                urls=temp_files,  # Pass temp files as "URLs" (file paths)
                llm_model_name=podcast_config.get('llm_model'),
                api_key_label=podcast_config.get('api_key_label'),
                tts_model=podcast_config.get('tts_model'),
                conversation_style=podcast_config.get('conversation_style', 'informative'),
                output_file=output_path
            )
            
            # Clean up temp files
            for temp_file in temp_files:
                os.remove(temp_file)
            os.rmdir(temp_dir)
            
            logger.info(f"Podcast generated successfully: {output_path}")
            return True, output_path
            
        except Exception as e:
            logger.error(f"Error generating podcast: {e}")
            return False, str(e)
    
    def estimate_duration(self, content_sources: List[str]) -> int:
        """
        Estimate podcast duration based on content.
        
        Returns estimated duration in minutes.
        """
        # Rough estimation: 150 words per minute speaking rate
        total_words = sum(len(content.split()) for content in content_sources)
        
        # Add overhead for conversation structure
        conversation_overhead = 1.5  # Conversations are longer than straight reading
        
        estimated_minutes = (total_words / 150) * conversation_overhead
        
        # Clamp to reasonable range
        return max(5, min(30, int(estimated_minutes)))

def main():
    """CLI entry point for podcast generation"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate podcast from Regen Network weekly digest"
    )
    parser.add_argument(
        '--input',
        required=True,
        help='Input weekly digest JSON file'
    )
    parser.add_argument(
        '--output',
        help='Output audio file path (default: auto-generated)'
    )
    parser.add_argument(
        '--config',
        default='config/audio_generation.json',
        help='Configuration file path'
    )
    parser.add_argument(
        '--model',
        help='Override LLM model (e.g., gpt-4, claude-3)'
    )
    parser.add_argument(
        '--length',
        choices=['short', 'medium', 'long'],
        help='Override podcast length'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Prepare content without generating audio'
    )
    
    args = parser.parse_args()
    
    # Load digest data
    with open(args.input, 'r') as f:
        digest_data = json.load(f)
    
    # Initialize generator
    generator = PodcastfyGenerator(args.config)
    
    # Prepare custom config
    custom_config = {}
    if args.model:
        custom_config.setdefault('podcastfy', {})['llm_model'] = args.model
    if args.length:
        custom_config.setdefault('podcastfy', {})['podcast_length'] = args.length
    
    if args.dry_run:
        # Just prepare and preview content
        content_sources = generator.prepare_content_for_podcast(digest_data)
        
        print("\n" + "="*60)
        print("DRY RUN - Content Preview")
        print("="*60)
        
        for i, content in enumerate(content_sources, 1):
            print(f"\n--- Source {i} ---")
            print(content[:500] + "..." if len(content) > 500 else content)
        
        duration = generator.estimate_duration(content_sources)
        print(f"\n--- Estimated Duration: {duration} minutes ---")
        
    else:
        # Generate podcast
        success, result = generator.generate_podcast(
            digest_data,
            output_path=args.output,
            custom_config=custom_config
        )
        
        if success:
            print(f"\n✅ Podcast generated successfully!")
            print(f"   Output: {result}")
            print("\n📝 Next steps:")
            print("   1. Review the generated audio")
            print("   2. Upload to podcast hosting platform")
            print("   3. Distribute to Pathway to Planetary Regeneration feed")
        else:
            print(f"\n❌ Failed to generate podcast: {result}")
            sys.exit(1)

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Simple Podcast Generator using OpenAI TTS
Generates audio podcast from weekly digest using OpenAI's text-to-speech API
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI not installed. Install with: pip install openai")

class SimplePodcastGenerator:
    """Generate podcast using OpenAI TTS directly"""

    def __init__(self):
        if not OPENAI_AVAILABLE:
            raise ImportError("OpenAI library required. Install with: pip install openai")

        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment")

        self.client = OpenAI(api_key=api_key)
        self.output_dir = Path("podcast_audio")
        self.output_dir.mkdir(exist_ok=True)

    def generate_from_digest(self, digest_path: str) -> str:
        """Generate podcast from digest JSON file"""

        # Load digest
        with open(digest_path, 'r') as f:
            digest = json.load(f)

        logger.info(f"Loaded digest with {len(digest.get('top_stories', []))} stories")

        # Create script from digest
        script = self._create_script(digest)
        logger.info(f"Created script: {len(script)} characters")

        # Generate audio
        output_file = self._generate_audio(script, digest)
        logger.info(f"Generated podcast: {output_file}")

        return output_file

    def _create_script(self, digest: Dict[str, Any]) -> str:
        """Create podcast script from digest"""

        lines = []

        # Introduction
        lines.append("Welcome to the Regen Network Weekly Digest podcast.")
        lines.append(f"This week covers {digest.get('week_start', 'recent')} through {digest.get('week_end', 'today')}.")
        lines.append("")

        # Executive summary
        if digest.get('executive_summary'):
            lines.append("Here's this week's summary:")
            lines.append(digest['executive_summary'])
            lines.append("")

        # Top stories
        stories = digest.get('top_stories', [])[:5]  # Limit to top 5
        if stories:
            lines.append(f"Let's dive into this week's top {len(stories)} stories.")
            lines.append("")

            for i, story in enumerate(stories, 1):
                lines.append(f"Story number {i}: {story.get('title', 'Untitled')}")

                # Clean up content
                content = story.get('content', '')
                # Remove markdown formatting
                content = content.replace('#', '').replace('*', '').replace('_', '')
                # Limit length
                if len(content) > 500:
                    content = content[:500] + "..."

                lines.append(content)
                lines.append("")

        # Closing
        lines.append("That concludes this week's Regen Network digest.")
        lines.append("To learn more, visit regen.network or join the discussion at forum.regen.network.")
        lines.append("Thank you for listening to the Pathway to Planetary Regeneration.")

        return "\n".join(lines)

    def _generate_audio(self, script: str, digest: Dict) -> str:
        """Generate audio using OpenAI TTS"""

        # Output filename
        week_end = digest.get('week_end', datetime.now().strftime('%Y-%m-%d'))
        output_file = self.output_dir / f"regen_weekly_{week_end}.mp3"

        try:
            # Generate speech
            response = self.client.audio.speech.create(
                model="tts-1",  # or "tts-1-hd" for higher quality
                voice="nova",   # or "alloy", "echo", "fable", "onyx", "shimmer"
                input=script,
                speed=1.0       # 0.25 to 4.0
            )

            # Save to file
            response.stream_to_file(str(output_file))

            return str(output_file)

        except Exception as e:
            logger.error(f"Failed to generate audio: {e}")

            # Create placeholder file
            placeholder = output_file.with_suffix('.txt')
            with open(placeholder, 'w') as f:
                f.write(f"PODCAST SCRIPT\n{'='*50}\n\n{script}\n\n")
                f.write(f"Error generating audio: {e}\n")

            return str(placeholder)

def main():
    """Generate podcast from latest weekly digest"""

    # Find latest digest
    digest_dir = Path("output/weekly")
    json_files = list(digest_dir.glob("weekly_digest_*.json"))

    if not json_files:
        print("No weekly digest found!")
        return

    latest_digest = max(json_files, key=lambda x: x.stat().st_mtime)
    print(f"📚 Using digest: {latest_digest}")

    # Generate podcast
    try:
        generator = SimplePodcastGenerator()
        audio_file = generator.generate_from_digest(str(latest_digest))
        print(f"✅ Podcast generated: {audio_file}")

        # Show file info
        if audio_file.endswith('.mp3'):
            size_mb = Path(audio_file).stat().st_size / (1024 * 1024)
            print(f"📊 File size: {size_mb:.1f} MB")

    except Exception as e:
        print(f"❌ Failed to generate podcast: {e}")
        print("\n💡 Make sure OPENAI_API_KEY is set in .env file")

if __name__ == "__main__":
    main()
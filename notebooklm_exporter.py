#!/usr/bin/env python3
"""
NotebookLM Export Module for Weekly Digest

Formats weekly digest content for Google NotebookLM ingestion.
Creates structured source documents optimized for audio overview generation.
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Any
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NotebookLMExporter:
    """Export weekly digest to NotebookLM-compatible format"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize exporter with configuration"""
        self.config = config or {
            "max_sources": 50,
            "source_format": "markdown",
            "include_metadata": True,
            "chunk_size": 2000,
            "sections": [
                "executive_summary",
                "top_stories",
                "governance_updates",
                "ecocredit_activity",
                "community_highlights",
                "technical_developments",
                "upcoming_events"
            ]
        }
    
    def format_for_notebooklm(self, digest_data: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Format digest data into NotebookLM source documents.
        
        Returns a list of source documents, each optimized for NotebookLM processing.
        """
        sources = []
        
        # 1. Executive Summary Document
        exec_summary = self._create_executive_summary(digest_data)
        sources.append({
            "title": "Weekly Executive Summary",
            "content": exec_summary,
            "type": "summary",
            "metadata": {
                "week_start": digest_data.get("week_start", ""),
                "week_end": digest_data.get("week_end", ""),
                "total_items": digest_data.get("total_items", 0)
            }
        })
        
        # 2. Top Stories Documents (chunked)
        top_stories = digest_data.get("top_stories", [])
        story_chunks = self._chunk_stories(top_stories)
        for i, chunk in enumerate(story_chunks, 1):
            sources.append({
                "title": f"Top Stories Part {i}",
                "content": chunk,
                "type": "stories",
                "metadata": {
                    "part": i,
                    "total_parts": len(story_chunks)
                }
            })
        
        # 3. Thematic Analysis Documents
        clusters = digest_data.get("clusters", [])
        for cluster in clusters[:10]:  # Top 10 themes
            theme_doc = self._create_theme_document(cluster)
            sources.append({
                "title": f"Theme: {cluster.get('theme', 'General')}",
                "content": theme_doc,
                "type": "theme",
                "metadata": {
                    "theme": cluster.get('theme'),
                    "item_count": cluster.get('size', 0)
                }
            })
        
        # 4. Statistics and Metrics
        stats_doc = self._create_stats_document(digest_data.get("stats", {}))
        sources.append({
            "title": "Weekly Statistics and Metrics",
            "content": stats_doc,
            "type": "statistics",
            "metadata": digest_data.get("stats", {})
        })
        
        # 5. Citations and References
        citations = digest_data.get("citations", [])
        if citations:
            citations_doc = self._create_citations_document(citations)
            sources.append({
                "title": "References and Citations",
                "content": citations_doc,
                "type": "references",
                "metadata": {
                    "citation_count": len(citations)
                }
            })
        
        # Limit to max sources
        max_sources = self.config.get("max_sources", 50)
        if len(sources) > max_sources:
            sources = sources[:max_sources]
            logger.warning(f"Truncated sources from {len(sources)} to {max_sources}")
        
        return sources
    
    def _create_executive_summary(self, digest_data: Dict[str, Any]) -> str:
        """Create executive summary document"""
        lines = []
        
        lines.append("# Regen Network Weekly Executive Summary\n")
        
        week_start = digest_data.get("week_start", "")
        week_end = digest_data.get("week_end", "")
        if week_start and week_end:
            lines.append(f"**Period:** {week_start} to {week_end}\n")
        
        lines.append("\n## Key Highlights\n")
        
        # Extract key metrics
        total_items = digest_data.get("total_items", 0)
        clusters = digest_data.get("clusters", [])
        
        lines.append(f"This week, the Regen Network ecosystem saw {total_items} significant updates. ")
        
        if clusters:
            top_themes = [c.get('theme', 'Updates') for c in clusters[:3]]
            lines.append(f"The primary focus areas were {', '.join(top_themes)}. ")
        
        # Add narrative context
        lines.append("\n\n## Strategic Context\n")
        lines.append("The Regen Network continues to advance its mission of planetary regeneration ")
        lines.append("through coordinated action across technology, governance, and community engagement. ")
        lines.append("This week's developments reflect growing momentum in regenerative finance ")
        lines.append("and ecological data infrastructure.\n")
        
        # Key developments
        lines.append("\n## Key Developments\n")
        top_stories = digest_data.get("top_stories", [])
        for i, story in enumerate(top_stories[:5], 1):
            lines.append(f"{i}. **{story.get('title', 'Update')}** - {story.get('source', 'Source')}\n")
        
        return ''.join(lines)
    
    def _chunk_stories(self, stories: List[Dict[str, Any]]) -> List[str]:
        """Chunk stories into manageable documents"""
        chunks = []
        current_chunk = []
        current_size = 0
        chunk_size = self.config.get("chunk_size", 2000)
        
        for story in stories:
            story_text = self._format_story(story)
            story_size = len(story_text)
            
            if current_size + story_size > chunk_size and current_chunk:
                # Save current chunk
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = [story_text]
                current_size = story_size
            else:
                current_chunk.append(story_text)
                current_size += story_size
        
        # Add remaining chunk
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))
        
        return chunks
    
    def _format_story(self, story: Dict[str, Any]) -> str:
        """Format a single story for NotebookLM"""
        lines = []
        
        lines.append(f"## {story.get('title', 'Untitled')}\n")
        
        # Metadata
        lines.append(f"**Source:** {story.get('source', 'Unknown')}\n")
        lines.append(f"**Date:** {story.get('publication_date', 'N/A')}\n")
        
        if story.get('tags'):
            lines.append(f"**Tags:** {', '.join(story['tags'][:5])}\n")
        
        lines.append("\n")
        
        # Content
        content = story.get('content', '')
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(content)
        
        # URL if available
        if story.get('url'):
            lines.append(f"\n\n[Read more]({story['url']})")
        
        return ''.join(lines)
    
    def _create_theme_document(self, cluster: Dict[str, Any]) -> str:
        """Create document for a thematic cluster"""
        lines = []
        
        theme = cluster.get('theme', 'General Updates')
        lines.append(f"# Theme: {theme}\n")
        
        lines.append(f"\nThis cluster contains {cluster.get('size', 0)} related items.\n")
        
        lines.append("\n## Key Items\n")
        for item in cluster.get('items', []):
            lines.append(f"- **{item.get('title', 'Item')}** ({item.get('source', 'Source')})\n")
        
        lines.append("\n## Analysis\n")
        lines.append(f"The {theme} theme represents coordinated activity across multiple ")
        lines.append("areas of the Regen Network ecosystem. This clustering suggests ")
        lines.append("focused attention on specific initiatives and outcomes.\n")
        
        return ''.join(lines)
    
    def _create_stats_document(self, stats: Dict[str, Any]) -> str:
        """Create statistics document"""
        lines = []
        
        lines.append("# Weekly Statistics and Metrics\n")
        
        lines.append("\n## Content Metrics\n")
        lines.append(f"- **Total Items Analyzed:** {stats.get('total_items', 0)}\n")
        lines.append(f"- **Unique Sources:** {stats.get('unique_sources', 0)}\n")
        lines.append(f"- **Most Active Source:** {stats.get('most_active_source', 'N/A')}\n")
        lines.append(f"- **Average Confidence Score:** {stats.get('avg_confidence', 0):.2f}\n")
        
        lines.append("\n## Source Distribution\n")
        source_dist = stats.get('source_distribution', {})
        for source, count in sorted(source_dist.items(), key=lambda x: x[1], reverse=True)[:10]:
            lines.append(f"- {source}: {count} items\n")
        
        lines.append("\n## Data Quality\n")
        lines.append(f"- **Minimum Confidence:** {stats.get('min_confidence', 0):.2f}\n")
        lines.append(f"- **Maximum Confidence:** {stats.get('max_confidence', 0):.2f}\n")
        
        return ''.join(lines)
    
    def _create_citations_document(self, citations: List[Dict[str, str]]) -> str:
        """Create citations document"""
        lines = []
        
        lines.append("# References and Citations\n")
        lines.append("\nComplete list of sources referenced in this weekly digest.\n")
        
        for i, citation in enumerate(citations, 1):
            lines.append(f"\n{i}. **{citation.get('title', 'Untitled')}**\n")
            lines.append(f"   - Source: {citation.get('source', 'Unknown')}\n")
            lines.append(f"   - Date: {citation.get('date', 'N/A')}\n")
            if citation.get('url'):
                lines.append(f"   - URL: {citation['url']}\n")
        
        return ''.join(lines)
    
    def export_to_files(self, sources: List[Dict[str, str]], output_dir: str):
        """Export NotebookLM sources to individual files"""
        os.makedirs(output_dir, exist_ok=True)
        
        # Create manifest
        manifest = {
            "generated_at": datetime.now().isoformat(),
            "source_count": len(sources),
            "sources": []
        }
        
        for i, source in enumerate(sources, 1):
            # Generate filename
            source_type = source.get('type', 'document')
            filename = f"{i:02d}_{source_type}.md"
            filepath = os.path.join(output_dir, filename)
            
            # Write content
            with open(filepath, 'w') as f:
                f.write(source['content'])
            
            # Add to manifest
            manifest['sources'].append({
                "filename": filename,
                "title": source.get('title', 'Document'),
                "type": source_type,
                "metadata": source.get('metadata', {})
            })
            
            logger.info(f"Exported: {filename}")
        
        # Write manifest
        manifest_path = os.path.join(output_dir, "manifest.json")
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)
        
        logger.info(f"Export complete: {len(sources)} sources in {output_dir}")
        return manifest_path

def main():
    """Test NotebookLM export functionality"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Export weekly digest for NotebookLM")
    parser.add_argument('--input', required=True, help="Input JSON digest file")
    parser.add_argument('--output-dir', default="output/notebooklm", help="Output directory")
    
    args = parser.parse_args()
    
    # Load digest
    with open(args.input, 'r') as f:
        digest_data = json.load(f)
    
    # Export to NotebookLM format
    exporter = NotebookLMExporter()
    sources = exporter.format_for_notebooklm(digest_data)
    
    # Save to files
    manifest_path = exporter.export_to_files(sources, args.output_dir)
    
    print(f"\n✅ NotebookLM export complete!")
    print(f"   Sources: {len(sources)}")
    print(f"   Directory: {args.output_dir}")
    print(f"   Manifest: {manifest_path}")
    print("\n📝 Next steps:")
    print("   1. Upload the markdown files to Google NotebookLM")
    print("   2. Generate outline and brief")
    print("   3. Create Audio Overview (20 minutes)")

if __name__ == "__main__":
    main()
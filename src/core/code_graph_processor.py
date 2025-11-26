#!/usr/bin/env python3
"""
Code Graph Processor - Extracts code entities from GitHub events
and loads them into Apache AGE graph database.

Runs parallel to Event Bridge v2:
- Event Bridge v2: Documents → chunks → embeddings (pgvector)
- Code Graph Processor: Source code → entities → graph (Apache AGE)

Based on multi_lang_extractor.py and load_multi_entities.py from regen-koi-mcp
"""

import asyncio
import psycopg2
import re
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
import logging
from pathlib import Path

# Tree-sitter imports (with fallback to regex if not available)
try:
    from tree_sitter import Language, Parser, Node
    import tree_sitter_go
    import tree_sitter_python
    import tree_sitter_typescript
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    logging.warning("Tree-sitter not available, falling back to regex-based extraction")

logger = logging.getLogger(__name__)


@dataclass
class CodeEntity:
    """Represents an extracted code entity"""
    entity_type: str  # Function, Class, Interface, Handler, Sensor, Keeper, Message, etc.
    name: str
    file_path: str
    line_number: int
    language: str
    repo: str
    docstring: Optional[str] = None
    fields: Optional[List[str]] = None
    methods: Optional[List[str]] = None
    properties: Optional[List[str]] = None

    def to_dict(self) -> Dict:
        """Convert to dict for JSON serialization"""
        return {k: v for k, v in asdict(self).items() if v is not None}


class CodeEntityExtractor:
    """Extracts code entities using tree-sitter (preferred) or regex (fallback)"""

    def __init__(self):
        self.use_tree_sitter = TREE_SITTER_AVAILABLE
        if self.use_tree_sitter:
            self.go_parser = Parser(Language(tree_sitter_go.language()))
            self.python_parser = Parser(Language(tree_sitter_python.language()))
            self.ts_parser = Parser(Language(tree_sitter_typescript.language_typescript()))
            self.tsx_parser = Parser(Language(tree_sitter_typescript.language_tsx()))
            logger.info("Using tree-sitter for code entity extraction")
        else:
            logger.info("Using regex-based code entity extraction")

    def extract_entities(self, content: str, file_path: str, language: str, repo: str) -> List[CodeEntity]:
        """Extract code entities from source content"""
        if self.use_tree_sitter:
            if language == 'python':
                return self._extract_python_tree_sitter(content, file_path, repo)
            elif language == 'go':
                return self._extract_go_tree_sitter(content, file_path, repo)
            elif language in ('typescript', 'javascript'):
                return self._extract_ts_tree_sitter(content, file_path, repo)
        
        # Fallback to regex
        if language == 'python':
            return self._extract_python_regex(content, file_path, repo)
        elif language == 'go':
            return self._extract_go_regex(content, file_path, repo)
        elif language in ('typescript', 'javascript'):
            return self._extract_ts_regex(content, file_path, repo)
        
        return []

    # ============= Regex-based extraction (fallback) =============
    
    def _extract_python_regex(self, content: str, file_path: str, repo: str) -> List[CodeEntity]:
        """Extract Python functions and classes using regex"""
        entities = []
        lines = content.split('\n')

        for i, line in enumerate(lines, 1):
            # Functions
            match = re.match(r'^(?:async\s+)?def\s+(\w+)\s*\(', line)
            if match and not match.group(1).startswith('_'):
                entities.append(CodeEntity(
                    entity_type='Function',
                    name=match.group(1),
                    file_path=file_path,
                    line_number=i,
                    language='python',
                    repo=repo,
                ))

            # Classes
            match = re.match(r'^class\s+(\w+)', line)
            if match:
                name = match.group(1)
                entity_type = self._classify_python_class(name, file_path)
                entities.append(CodeEntity(
                    entity_type=entity_type,
                    name=name,
                    file_path=file_path,
                    line_number=i,
                    language='python',
                    repo=repo,
                ))

        return entities

    def _classify_python_class(self, name: str, file_path: str) -> str:
        """Classify a Python class by name and file path"""
        name_lower = name.lower()
        if 'handler' in name_lower or 'keeper' in name_lower:
            return 'Handler'
        elif 'processor' in name_lower:
            return 'Processor'
        elif 'sensor' in name_lower:
            return 'Sensor'
        elif 'client' in name_lower:
            return 'Client'
        return 'Class'

    def _extract_go_regex(self, content: str, file_path: str, repo: str) -> List[CodeEntity]:
        """Extract Go functions, structs, interfaces using regex"""
        entities = []
        lines = content.split('\n')

        for i, line in enumerate(lines, 1):
            # Functions
            match = re.match(r'^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(', line)
            if match:
                entities.append(CodeEntity(
                    entity_type='Function',
                    name=match.group(1),
                    file_path=file_path,
                    line_number=i,
                    language='go',
                    repo=repo,
                ))

            # Structs
            match = re.match(r'^type\s+(\w+)\s+struct', line)
            if match:
                name = match.group(1)
                entity_type = self._classify_go_entity(name, file_path)
                if entity_type:
                    entities.append(CodeEntity(
                        entity_type=entity_type,
                        name=name,
                        file_path=file_path,
                        line_number=i,
                        language='go',
                        repo=repo,
                    ))

            # Interfaces
            match = re.match(r'^type\s+(\w+)\s+interface', line)
            if match:
                entities.append(CodeEntity(
                    entity_type='Interface',
                    name=match.group(1),
                    file_path=file_path,
                    line_number=i,
                    language='go',
                    repo=repo,
                ))

        return entities

    def _classify_go_entity(self, name: str, file_path: str) -> Optional[str]:
        """Classify a Go struct as Keeper, Msg, or Event"""
        if name == 'Keeper' and 'keeper' in file_path.lower():
            return 'Keeper'
        elif name.startswith('Msg') and not name.endswith('Response'):
            return 'Message'
        elif name.startswith('Event'):
            return 'Event'
        elif name.startswith('Query') and name.endswith('Request'):
            return 'Query'
        return 'Class'  # Default for other structs

    def _extract_ts_regex(self, content: str, file_path: str, repo: str) -> List[CodeEntity]:
        """Extract TypeScript/JavaScript entities using regex"""
        entities = []
        lines = content.split('\n')

        for i, line in enumerate(lines, 1):
            # Functions
            match = re.match(r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)', line)
            if match:
                entities.append(CodeEntity(
                    entity_type='Function',
                    name=match.group(1),
                    file_path=file_path,
                    line_number=i,
                    language='typescript',
                    repo=repo,
                ))

            # Classes
            match = re.match(r'^(?:export\s+)?class\s+(\w+)', line)
            if match:
                name = match.group(1)
                entity_type = 'Sensor' if 'Sensor' in name else 'Handler' if 'Handler' in name else 'Class'
                entities.append(CodeEntity(
                    entity_type=entity_type,
                    name=name,
                    file_path=file_path,
                    line_number=i,
                    language='typescript',
                    repo=repo,
                ))

            # Interfaces
            match = re.match(r'^(?:export\s+)?interface\s+(\w+)', line)
            if match:
                entities.append(CodeEntity(
                    entity_type='Interface',
                    name=match.group(1),
                    file_path=file_path,
                    line_number=i,
                    language='typescript',
                    repo=repo,
                ))

        return entities

    # ============= Tree-sitter based extraction (preferred) =============
    
    def _get_node_text(self, node: 'Node', source: bytes) -> str:
        """Extract text from a tree-sitter node"""
        return source[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')

    def _extract_python_tree_sitter(self, content: str, file_path: str, repo: str) -> List[CodeEntity]:
        """Extract Python entities using tree-sitter"""
        entities = []
        source = content.encode('utf-8')
        tree = self.python_parser.parse(source)

        def visit(node, depth=0):
            # Classes
            if node.type == 'class_definition':
                class_name = None
                methods = []
                
                for child in node.children:
                    if child.type == 'identifier':
                        class_name = self._get_node_text(child, source)
                    elif child.type == 'block':
                        for block_child in child.children:
                            if block_child.type == 'function_definition':
                                for func_child in block_child.children:
                                    if func_child.type == 'identifier':
                                        method_name = self._get_node_text(func_child, source)
                                        if not method_name.startswith('_'):
                                            methods.append(method_name)

                if class_name:
                    entity_type = self._classify_python_class(class_name, file_path)
                    entities.append(CodeEntity(
                        entity_type=entity_type,
                        name=class_name,
                        file_path=file_path,
                        line_number=node.start_point[0] + 1,
                        language='python',
                        repo=repo,
                        methods=methods[:10] if methods else None
                    ))

            # Top-level functions (depth 0 only)
            elif node.type == 'function_definition' and depth == 0:
                func_name = None
                for child in node.children:
                    if child.type == 'identifier':
                        func_name = self._get_node_text(child, source)
                        break

                if func_name and not func_name.startswith('_'):
                    entities.append(CodeEntity(
                        entity_type='Function',
                        name=func_name,
                        file_path=file_path,
                        line_number=node.start_point[0] + 1,
                        language='python',
                        repo=repo,
                    ))

            for child in node.children:
                new_depth = depth + 1 if node.type in ['class_definition', 'function_definition'] else depth
                visit(child, new_depth)

        visit(tree.root_node)
        return entities

    def _extract_go_tree_sitter(self, content: str, file_path: str, repo: str) -> List[CodeEntity]:
        """Extract Go entities using tree-sitter"""
        entities = []
        source = content.encode('utf-8')
        tree = self.go_parser.parse(source)

        def visit(node):
            if node.type == 'type_declaration':
                for child in node.children:
                    if child.type == 'type_spec':
                        type_name = None
                        struct_type = None

                        for spec_child in child.children:
                            if spec_child.type == 'type_identifier':
                                type_name = self._get_node_text(spec_child, source)
                            elif spec_child.type == 'struct_type':
                                struct_type = spec_child

                        if type_name and struct_type:
                            entity_type = self._classify_go_entity(type_name, file_path)
                            if entity_type:
                                entities.append(CodeEntity(
                                    entity_type=entity_type,
                                    name=type_name,
                                    file_path=file_path,
                                    line_number=node.start_point[0] + 1,
                                    language='go',
                                    repo=repo,
                                ))

            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return entities

    def _extract_ts_tree_sitter(self, content: str, file_path: str, repo: str) -> List[CodeEntity]:
        """Extract TypeScript entities using tree-sitter"""
        entities = []
        source = content.encode('utf-8')
        
        parser = self.tsx_parser if file_path.endswith('.tsx') else self.ts_parser
        tree = parser.parse(source)

        def visit(node):
            # Classes
            if node.type == 'class_declaration':
                class_name = None
                methods = []

                for child in node.children:
                    if child.type == 'type_identifier':
                        class_name = self._get_node_text(child, source)
                    elif child.type == 'class_body':
                        for body_child in child.children:
                            if body_child.type == 'method_definition':
                                for method_child in body_child.children:
                                    if method_child.type == 'property_identifier':
                                        methods.append(self._get_node_text(method_child, source))

                if class_name:
                    entity_type = 'Sensor' if 'Sensor' in class_name else 'Handler' if 'Handler' in class_name else 'Class'
                    entities.append(CodeEntity(
                        entity_type=entity_type,
                        name=class_name,
                        file_path=file_path,
                        line_number=node.start_point[0] + 1,
                        language='typescript',
                        repo=repo,
                        methods=methods[:10] if methods else None
                    ))

            # Interfaces
            elif node.type == 'interface_declaration':
                interface_name = None
                properties = []

                for child in node.children:
                    if child.type == 'type_identifier':
                        interface_name = self._get_node_text(child, source)
                    elif child.type == 'object_type':
                        for prop in child.children:
                            if prop.type == 'property_signature':
                                for prop_child in prop.children:
                                    if prop_child.type == 'property_identifier':
                                        properties.append(self._get_node_text(prop_child, source))

                if interface_name:
                    entities.append(CodeEntity(
                        entity_type='Interface',
                        name=interface_name,
                        file_path=file_path,
                        line_number=node.start_point[0] + 1,
                        language='typescript',
                        repo=repo,
                        properties=properties[:10] if properties else None
                    ))

            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return entities


class CodeGraphProcessor:
    """Processes GitHub sensor events to extract code entities into graph"""

    def __init__(self, db_config: Dict[str, Any], graph_name: str = 'regen_graph'):
        self.db_config = db_config
        self.graph_name = graph_name
        self.conn = None
        self.extractor = CodeEntityExtractor()

        # File extensions to process for code entities
        self.code_extensions = {
            '.py': 'python',
            '.go': 'go',
            '.ts': 'typescript',
            '.tsx': 'typescript',
            '.js': 'javascript',
            '.jsx': 'javascript',
        }

        # Repos to process for code graph (configurable via env)
        self.graph_enabled_repos = [
            'regen-ledger',
            'regen-web',
            'GAIA',
            'koi-sensors',
            'koi-processor',
            'koi-research',
            'regen-koi-mcp',
        ]

    def should_process_for_graph(self, event: Dict) -> bool:
        """Determine if this event should be processed for code graph"""
        # Check if it's from GitHub sensor
        source = event.get('source_node', event.get('source_sensor', ''))
        if 'github' not in source.lower():
            return False

        # Get file path from bundle
        bundle = event.get('bundle', {})
        if not bundle:
            return False

        manifest = bundle.get('manifest', {})
        metadata = manifest.get('metadata', {})
        file_path = metadata.get('file_path', '')

        if not file_path:
            return False

        # Check file extension
        ext = '.' + file_path.split('.')[-1] if '.' in file_path else ''
        if ext not in self.code_extensions:
            return False

        # Skip test files
        if any(skip in file_path.lower() for skip in ['_test.', 'test_', '.spec.', '.test.', '/test/', '/tests/']):
            return False

        # Check if repo is enabled for graph
        repo = self.extract_repo_from_path(file_path)
        if repo not in self.graph_enabled_repos:
            return False

        return True

    def extract_repo_from_path(self, file_path: str) -> str:
        """Extract repository name from file path"""
        # Handle paths like: regen-repos/regen-ledger/... or /opt/projects/regen-repos/regen-ledger/...
        match = re.search(r'regen-repos/([^/]+)', file_path)
        if match:
            return match.group(1)

        for repo_name in self.graph_enabled_repos:
            if f'/{repo_name}/' in file_path or file_path.startswith(f'{repo_name}/'):
                return repo_name

        parts = [p for p in file_path.split('/') if p]
        return parts[0] if parts else 'unknown'

    async def process_event(self, event: Dict) -> Dict[str, Any]:
        """Process a single GitHub event for code entities"""
        result = {'processed': False, 'entities_loaded': 0, 'error': None}

        try:
            if not self.should_process_for_graph(event):
                return result

            bundle = event.get('bundle', {})
            contents = bundle.get('contents', {})
            document = contents.get('document', {})
            content = document.get('content', '')

            if not content:
                return result

            manifest = bundle.get('manifest', {})
            metadata = manifest.get('metadata', {})
            file_path = metadata.get('file_path', '')

            ext = '.' + file_path.split('.')[-1] if '.' in file_path else ''
            language = self.code_extensions.get(ext, 'unknown')
            repo = self.extract_repo_from_path(file_path)

            # Extract entities from source code
            entities = self.extractor.extract_entities(content, file_path, language, repo)

            if not entities:
                return result

            # Load into graph
            loaded = await self.load_entities_to_graph(entities)

            logger.info(f"Processed {file_path}: extracted {len(entities)} entities, loaded {loaded}")
            result['processed'] = True
            result['entities_loaded'] = loaded

        except Exception as e:
            logger.error(f"Error processing event for code graph: {e}", exc_info=True)
            result['error'] = str(e)

        return result

    async def load_entities_to_graph(self, entities: List[CodeEntity]) -> int:
        """Load extracted entities into Apache AGE graph"""
        if not entities:
            return 0

        if not self.conn:
            self.conn = psycopg2.connect(**self.db_config)

        cursor = self.conn.cursor()
        cursor.execute("LOAD 'age';")
        cursor.execute('SET search_path = ag_catalog, public;')

        loaded = 0
        for entity in entities:
            try:
                # Escape strings for Cypher
                name = self.escape_cypher(entity.name)
                file_path = self.escape_cypher(entity.file_path)
                docstring = self.escape_cypher(entity.docstring or '')[:500]

                # Build extra properties
                extra_props = []
                if entity.methods:
                    methods_json = json.dumps(entity.methods[:10])
                    extra_props.append(f"methods: '{self.escape_cypher(methods_json)}'")
                if entity.fields:
                    fields_json = json.dumps(entity.fields[:10])
                    extra_props.append(f"fields: '{self.escape_cypher(fields_json)}'")
                if entity.properties:
                    props_json = json.dumps(entity.properties[:10])
                    extra_props.append(f"properties: '{self.escape_cypher(props_json)}'")

                extra_props_str = ', ' + ', '.join(extra_props) if extra_props else ''

                # Use entity_type as label
                label = entity.entity_type.replace(' ', '_')

                query = f"""
                SELECT * FROM cypher('{self.graph_name}', $$
                    MERGE (n:{label} {{name: '{name}', file_path: '{file_path}', repo: '{entity.repo}'}})
                    SET n.line_number = {entity.line_number},
                        n.language = '{entity.language}',
                        n.docstring = '{docstring}'{extra_props_str}
                    RETURN n
                $$) as (n agtype);
                """

                cursor.execute(query)
                self.conn.commit()
                loaded += 1

            except Exception as e:
                logger.error(f"Error loading entity {entity.name}: {e}")
                self.conn.rollback()

        # Ensure Repository node exists
        repos = set(e.repo for e in entities)
        for repo in repos:
            try:
                query = f"""
                SELECT * FROM cypher('{self.graph_name}', $$
                    MERGE (r:Repository {{name: '{repo}'}})
                    RETURN r
                $$) as (r agtype);
                """
                cursor.execute(query)
                self.conn.commit()
            except Exception as e:
                logger.error(f"Error creating Repository node for {repo}: {e}")
                self.conn.rollback()

        return loaded

    def escape_cypher(self, s: str) -> str:
        """Escape string for Cypher query"""
        if s is None:
            return ''
        return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").replace("\r", "")

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()


# Entry point for integration with event processing
async def process_github_event_for_graph(event: Dict, db_config: Dict) -> Dict[str, Any]:
    """Entry point for processing a GitHub event for code graph"""
    processor = CodeGraphProcessor(db_config)
    try:
        return await processor.process_event(event)
    finally:
        processor.close()

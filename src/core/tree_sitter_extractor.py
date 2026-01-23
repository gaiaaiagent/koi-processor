"""
Tree-sitter based code entity extractor for Phase 1
Replaces regex-based extraction with proper AST parsing

Supports:
- Go: functions, structs, interfaces, methods, imports
- TypeScript: functions, classes, interfaces, imports
- Python: functions, classes, imports

Phase 1 Goals:
- Extract entities with full metadata (signature, params, return type)
- Extract CALLS edges (function call relationships)
- Extract IMPORTS edges (module dependencies)
- Extract IMPLEMENTS edges (interface implementations)
- Deterministic node IDs for idempotent extraction
"""

import hashlib
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from loguru import logger

# Tree-sitter imports
import tree_sitter_go
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Parser, Node


@dataclass
class CodeEntity:
    """Represents an extracted code entity"""
    entity_id: str           # Deterministic hash ID
    name: str
    entity_type: str         # Function, Struct, Interface, Method, Class, Keeper, Message, Module, File
    file_path: str
    line_start: int
    line_end: int
    language: str
    repo: str
    signature: str = ""
    params: str = ""
    return_type: str = ""
    docstring: str = ""
    receiver_type: str = ""   # For methods (Go)
    extraction_method: str = "tree_sitter"
    module_name: str = ""     # Module/package this entity belongs to
    module_path: str = ""     # Full module path (for BELONGS_TO edges)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class CodeEdge:
    """Represents a relationship between entities"""
    edge_id: str             # Deterministic hash ID
    from_entity_id: str
    to_entity_id: str
    edge_type: str           # CALLS, IMPORTS, IMPLEMENTS
    file_path: str
    line_number: int

    def to_dict(self) -> Dict:
        return asdict(self)


def generate_entity_id(repo: str, file_path: str, name: str, signature: str = "") -> str:
    """Generate deterministic ID for idempotent extraction"""
    key = f"{repo}:{file_path}:{name}:{signature}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def generate_edge_id(from_id: str, to_id: str, edge_type: str) -> str:
    """Generate deterministic ID for edges"""
    key = f"{from_id}-{edge_type}->{to_id}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class TreeSitterExtractor:
    """
    Tree-sitter based code entity extractor

    Usage:
        extractor = TreeSitterExtractor()
        entities, edges = extractor.extract("go", code, "x/ecocredit/module.go", "regen-ledger")
    """

    def __init__(self):
        # Initialize parsers for each language
        self.go_parser = Parser(Language(tree_sitter_go.language()))
        self.python_parser = Parser(Language(tree_sitter_python.language()))
        self.ts_parser = Parser(Language(tree_sitter_typescript.language_typescript()))
        self.tsx_parser = Parser(Language(tree_sitter_typescript.language_tsx()))

        logger.info("TreeSitterExtractor initialized with Go, Python, TypeScript support")

    def extract(
        self,
        language: str,
        content: str,
        file_path: str,
        repo: str
    ) -> Tuple[List[CodeEntity], List[CodeEdge]]:
        """
        Extract entities and edges from source code

        Returns:
            Tuple of (entities, edges)
        """
        if language == "go":
            return self._extract_go(content, file_path, repo)
        elif language == "python":
            return self._extract_python(content, file_path, repo)
        elif language in ("typescript", "javascript", "tsx"):
            return self._extract_typescript(content, file_path, repo, language)
        else:
            logger.warning(f"Unsupported language: {language}")
            return [], []

    # ============= GO EXTRACTION =============

    def _extract_go(
        self,
        content: str,
        file_path: str,
        repo: str
    ) -> Tuple[List[CodeEntity], List[CodeEdge]]:
        """Extract entities and edges from Go code"""
        entities = []
        edges = []

        tree = self.go_parser.parse(content.encode())
        root = tree.root_node
        source = content.encode()

        # Track entities by name for edge creation
        entity_map = {}

        # Extract package name
        package_name = self._get_go_package(root, source)

        # Calculate module path from file_path and package name
        # For Go, module path is typically the directory path
        # e.g., x/ecocredit/base/keeper.go -> x/ecocredit/base
        import os
        dir_path = os.path.dirname(file_path)
        module_path = f"{repo}/{dir_path}" if dir_path else repo

        # Create Module entity for Go package
        if package_name:
            module_entity = self._create_go_module_entity(
                package_name, dir_path, file_path, repo, root, source
            )
            entities.append(module_entity)

        # Create File entity
        file_entity = self._create_file_entity(file_path, repo, "go", content, package_name, module_path)
        entities.append(file_entity)

        # Create BELONGS_TO edge from File to Module
        if package_name:
            module_id = generate_entity_id(repo, dir_path, package_name, "module")
            belongs_to_edge = CodeEdge(
                edge_id=generate_edge_id(file_entity.entity_id, module_id, "BELONGS_TO"),
                from_entity_id=file_entity.entity_id,
                to_entity_id=module_id,
                edge_type="BELONGS_TO",
                file_path=file_path,
                line_number=1,
            )
            edges.append(belongs_to_edge)

        # Extract imports
        imports = self._extract_go_imports(root, source, file_path, repo)
        for imp in imports:
            imp.module_name = package_name
            imp.module_path = module_path
        entities.extend(imports)

        # Extract type declarations (structs, interfaces)
        for node in self._find_nodes_by_type(root, "type_declaration"):
            entity = self._extract_go_type(node, source, file_path, repo)
            if entity:
                entity.module_name = package_name
                entity.module_path = module_path
                entities.append(entity)
                entity_map[entity.name] = entity

        # Extract function declarations
        for node in self._find_nodes_by_type(root, "function_declaration"):
            entity = self._extract_go_function(node, source, file_path, repo)
            if entity:
                entity.module_name = package_name
                entity.module_path = module_path
                entities.append(entity)
                entity_map[entity.name] = entity

                # Extract CALLS edges from function body
                calls = self._extract_go_calls(node, source, file_path, repo, entity)
                edges.extend(calls)

        # Extract method declarations
        for node in self._find_nodes_by_type(root, "method_declaration"):
            entity = self._extract_go_method(node, source, file_path, repo)
            if entity:
                entity.module_name = package_name
                entity.module_path = module_path
                entities.append(entity)
                # Use receiver.method as key
                method_key = f"{entity.receiver_type}.{entity.name}"
                entity_map[method_key] = entity

                # Extract CALLS edges from method body
                calls = self._extract_go_calls(node, source, file_path, repo, entity)
                edges.extend(calls)

        return entities, edges

    def _create_go_module_entity(
        self,
        package_name: str,
        dir_path: str,
        file_path: str,
        repo: str,
        root: Node,
        source: bytes
    ) -> CodeEntity:
        """Create a Module entity for a Go package"""
        import os

        # Get package clause location
        line_start = 1
        line_end = 1
        for node in root.children:
            if node.type == "package_clause":
                line_start = node.start_point[0] + 1
                line_end = node.end_point[0] + 1
                break

        # Full module path for uniqueness
        module_path = f"{repo}/{dir_path}" if dir_path else repo

        return CodeEntity(
            entity_id=generate_entity_id(repo, dir_path, package_name, "module"),
            name=package_name,
            entity_type="Module",
            file_path=dir_path or ".",  # Module represents directory
            line_start=line_start,
            line_end=line_end,
            language="go",
            repo=repo,
            signature=f"package {package_name}",
            docstring=f"Go package in {dir_path or 'root'}",
            module_name=package_name,
            module_path=module_path,
        )

    def _create_file_entity(
        self,
        file_path: str,
        repo: str,
        language: str,
        content: str,
        module_name: str = "",
        module_path: str = ""
    ) -> CodeEntity:
        """Create a File entity"""
        import os
        import hashlib

        file_name = os.path.basename(file_path)
        line_count = content.count('\n') + 1
        file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, file_name, "file"),
            name=file_name,
            entity_type="File",
            file_path=file_path,
            line_start=1,
            line_end=line_count,
            language=language,
            repo=repo,
            signature=f"{file_path} ({line_count} lines)",
            docstring=f"hash:{file_hash}",
            module_name=module_name,
            module_path=module_path,
        )

    def _get_go_package(self, root: Node, source: bytes) -> str:
        """Get Go package name"""
        for node in root.children:
            if node.type == "package_clause":
                for child in node.children:
                    if child.type == "package_identifier":
                        return source[child.start_byte:child.end_byte].decode()
        return ""

    def _extract_go_imports(
        self,
        root: Node,
        source: bytes,
        file_path: str,
        repo: str
    ) -> List[CodeEntity]:
        """Extract Go import statements"""
        entities = []

        for node in self._find_nodes_by_type(root, "import_declaration"):
            line_start = node.start_point[0] + 1
            line_end = node.end_point[0] + 1

            # Find import specs
            for spec_list in self._find_nodes_by_type(node, "import_spec_list"):
                for spec in self._find_nodes_by_type(spec_list, "import_spec"):
                    path_node = None
                    alias = None

                    for child in spec.children:
                        if child.type == "interpreted_string_literal":
                            path_node = child
                        elif child.type == "package_identifier":
                            alias = source[child.start_byte:child.end_byte].decode()
                        elif child.type == "blank_identifier":
                            alias = "_"
                        elif child.type == "dot":
                            alias = "."

                    if path_node:
                        import_path = source[path_node.start_byte:path_node.end_byte].decode().strip('"')
                        entity = CodeEntity(
                            entity_id=generate_entity_id(repo, file_path, f"import:{import_path}"),
                            name=import_path,
                            entity_type="Import",
                            file_path=file_path,
                            line_start=spec.start_point[0] + 1,
                            line_end=spec.end_point[0] + 1,
                            language="go",
                            repo=repo,
                            signature=f'import "{import_path}"' + (f" as {alias}" if alias else ""),
                        )
                        entities.append(entity)

            # Handle single import (no list)
            for spec in self._find_nodes_by_type(node, "import_spec"):
                if spec.parent.type != "import_spec_list":
                    path_node = None
                    for child in spec.children:
                        if child.type == "interpreted_string_literal":
                            path_node = child
                    if path_node:
                        import_path = source[path_node.start_byte:path_node.end_byte].decode().strip('"')
                        entity = CodeEntity(
                            entity_id=generate_entity_id(repo, file_path, f"import:{import_path}"),
                            name=import_path,
                            entity_type="Import",
                            file_path=file_path,
                            line_start=spec.start_point[0] + 1,
                            line_end=spec.end_point[0] + 1,
                            language="go",
                            repo=repo,
                            signature=f'import "{import_path}"',
                        )
                        entities.append(entity)

        return entities

    def _extract_go_type(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str
    ) -> Optional[CodeEntity]:
        """Extract Go struct or interface"""
        type_spec = None
        for child in node.children:
            if child.type == "type_spec":
                type_spec = child
                break

        if not type_spec:
            return None

        name = ""
        type_kind = ""
        fields = []

        for child in type_spec.children:
            if child.type == "type_identifier":
                name = source[child.start_byte:child.end_byte].decode()
            elif child.type == "struct_type":
                type_kind = "Struct"
                # Check for Cosmos SDK patterns
                if name.endswith("Keeper"):
                    type_kind = "Keeper"
                elif name.startswith("Msg") and not name.endswith("Response"):
                    type_kind = "Message"
                elif name.endswith("Query"):
                    type_kind = "Query"
            elif child.type == "interface_type":
                type_kind = "Interface"

        if not name or not type_kind:
            return None

        # Get docstring (comment above)
        docstring = self._get_go_docstring(node, source)

        signature = source[node.start_byte:node.end_byte].decode()
        # Truncate long signatures
        if len(signature) > 500:
            signature = signature[:500] + "..."

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, name, type_kind),
            name=name,
            entity_type=type_kind,
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language="go",
            repo=repo,
            signature=signature,
            docstring=docstring,
        )

    def _extract_go_function(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str
    ) -> Optional[CodeEntity]:
        """Extract Go function declaration"""
        name = ""
        params = ""
        return_type = ""

        for child in node.children:
            if child.type == "identifier":
                name = source[child.start_byte:child.end_byte].decode()
            elif child.type == "parameter_list":
                params = source[child.start_byte:child.end_byte].decode()
            elif child.type in ("type_identifier", "pointer_type", "slice_type",
                               "map_type", "channel_type", "function_type",
                               "qualified_type", "generic_type"):
                return_type = source[child.start_byte:child.end_byte].decode()
            elif child.type == "parameter_list" and return_type == "":
                # Second parameter_list is return type
                pass

        if not name:
            return None

        # Get docstring
        docstring = self._get_go_docstring(node, source)

        # Build signature (first line only)
        full_sig = source[node.start_byte:node.end_byte].decode()
        sig_lines = full_sig.split('\n')
        signature = sig_lines[0] if sig_lines else full_sig

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, name, params),
            name=name,
            entity_type="Function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language="go",
            repo=repo,
            signature=signature,
            params=params,
            return_type=return_type,
            docstring=docstring,
        )

    def _extract_go_method(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str
    ) -> Optional[CodeEntity]:
        """Extract Go method declaration (function with receiver)"""
        name = ""
        params = ""
        return_type = ""
        receiver_type = ""

        for child in node.children:
            if child.type == "parameter_list" and receiver_type == "":
                # First parameter_list is receiver
                receiver_text = source[child.start_byte:child.end_byte].decode()
                # Extract type from receiver, e.g., "(k Keeper)" -> "Keeper"
                # or "(k *Keeper)" -> "*Keeper"
                for param in self._find_nodes_by_type(child, "parameter_declaration"):
                    for pchild in param.children:
                        if pchild.type in ("type_identifier", "pointer_type"):
                            receiver_type = source[pchild.start_byte:pchild.end_byte].decode()
                            # Remove pointer prefix for matching
                            receiver_type = receiver_type.lstrip("*")
            elif child.type == "field_identifier":
                name = source[child.start_byte:child.end_byte].decode()
            elif child.type == "parameter_list" and receiver_type != "":
                params = source[child.start_byte:child.end_byte].decode()

        if not name or not receiver_type:
            return None

        # Get docstring
        docstring = self._get_go_docstring(node, source)

        # Build signature
        full_sig = source[node.start_byte:node.end_byte].decode()
        sig_lines = full_sig.split('\n')
        signature = sig_lines[0] if sig_lines else full_sig

        # Determine entity type (check for handler pattern)
        entity_type = "Method"
        if receiver_type.endswith("Keeper"):
            # Check if it handles a Msg
            if params and "Msg" in params:
                entity_type = "Handler"

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, f"{receiver_type}.{name}", params),
            name=name,
            entity_type=entity_type,
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language="go",
            repo=repo,
            signature=signature,
            params=params,
            return_type=return_type,
            receiver_type=receiver_type,
            docstring=docstring,
        )

    def _extract_go_calls(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str,
        caller: CodeEntity
    ) -> List[CodeEdge]:
        """Extract function/method calls from a function body"""
        edges = []

        # Find the function body
        body = None
        for child in node.children:
            if child.type == "block":
                body = child
                break

        if not body:
            return edges

        # Find all call expressions
        for call in self._find_nodes_by_type(body, "call_expression"):
            callee_name = ""
            callee_receiver = ""

            func_node = None
            for child in call.children:
                if child.type == "identifier":
                    callee_name = source[child.start_byte:child.end_byte].decode()
                elif child.type == "selector_expression":
                    # Method call: obj.method()
                    for sel_child in child.children:
                        if sel_child.type == "identifier":
                            callee_receiver = source[sel_child.start_byte:sel_child.end_byte].decode()
                        elif sel_child.type == "field_identifier":
                            callee_name = source[sel_child.start_byte:sel_child.end_byte].decode()

            if callee_name:
                # Generate edge
                if callee_receiver:
                    to_name = f"{callee_receiver}.{callee_name}"
                else:
                    to_name = callee_name

                edge = CodeEdge(
                    edge_id=generate_edge_id(caller.entity_id, to_name, "CALLS"),
                    from_entity_id=caller.entity_id,
                    to_entity_id=to_name,  # Will be resolved later
                    edge_type="CALLS",
                    file_path=file_path,
                    line_number=call.start_point[0] + 1,
                )
                edges.append(edge)

        return edges

    def _get_go_docstring(self, node: Node, source: bytes) -> str:
        """Get Go comment above a declaration"""
        comments = []

        # Look at previous siblings
        prev = node.prev_sibling
        while prev:
            if prev.type == "comment":
                comment_text = source[prev.start_byte:prev.end_byte].decode()
                # Remove // prefix
                comment_text = comment_text.lstrip("/ ").strip()
                comments.insert(0, comment_text)
                prev = prev.prev_sibling
            else:
                break

        return " ".join(comments)

    def _find_nodes_by_type(self, node: Node, type_name: str) -> List[Node]:
        """Recursively find all nodes of a given type"""
        results = []
        if node.type == type_name:
            results.append(node)
        for child in node.children:
            results.extend(self._find_nodes_by_type(child, type_name))
        return results

    # ============= PYTHON EXTRACTION =============

    def _extract_python(
        self,
        content: str,
        file_path: str,
        repo: str
    ) -> Tuple[List[CodeEntity], List[CodeEdge]]:
        """Extract entities and edges from Python code"""
        import os

        entities = []
        edges = []

        tree = self.python_parser.parse(content.encode())
        root = tree.root_node
        source = content.encode()

        # Calculate Python module name from file path
        # e.g., src/core/extractor.py -> src.core.extractor
        # or __init__.py -> parent directory name
        dir_path = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)

        if file_name == "__init__.py":
            # Package init file - module name is the directory
            module_name = os.path.basename(dir_path) if dir_path else repo
        else:
            # Regular module - use file name without extension
            module_name = os.path.splitext(file_name)[0]

        # Full module path (dotted notation)
        if dir_path:
            module_path = dir_path.replace(os.sep, ".").replace("/", ".")
            full_module_path = f"{module_path}.{module_name}" if module_name != os.path.basename(dir_path) else module_path
        else:
            full_module_path = module_name

        # Create Module entity for Python module
        module_entity = self._create_python_module_entity(
            module_name, dir_path, file_path, repo, content, full_module_path
        )
        entities.append(module_entity)

        # Create File entity
        file_entity = self._create_file_entity(file_path, repo, "python", content, module_name, full_module_path)
        entities.append(file_entity)

        # Create BELONGS_TO edge from File to Module
        belongs_to_edge = CodeEdge(
            edge_id=generate_edge_id(file_entity.entity_id, module_entity.entity_id, "BELONGS_TO"),
            from_entity_id=file_entity.entity_id,
            to_entity_id=module_entity.entity_id,
            edge_type="BELONGS_TO",
            file_path=file_path,
            line_number=1,
        )
        edges.append(belongs_to_edge)

        # Extract imports
        for node in self._find_nodes_by_type(root, "import_statement"):
            imports = self._extract_python_import(node, source, file_path, repo)
            for imp in imports:
                imp.module_name = module_name
                imp.module_path = full_module_path
            entities.extend(imports)
        for node in self._find_nodes_by_type(root, "import_from_statement"):
            imports = self._extract_python_import(node, source, file_path, repo)
            for imp in imports:
                imp.module_name = module_name
                imp.module_path = full_module_path
            entities.extend(imports)

        # Extract classes
        for node in self._find_nodes_by_type(root, "class_definition"):
            entity = self._extract_python_class(node, source, file_path, repo)
            if entity:
                entity.module_name = module_name
                entity.module_path = full_module_path
                entities.append(entity)

        # Extract functions
        for node in self._find_nodes_by_type(root, "function_definition"):
            entity = self._extract_python_function(node, source, file_path, repo)
            if entity:
                entity.module_name = module_name
                entity.module_path = full_module_path
                entities.append(entity)
                # Extract calls
                calls = self._extract_python_calls(node, source, file_path, repo, entity)
                edges.extend(calls)

        return entities, edges

    def _create_python_module_entity(
        self,
        module_name: str,
        dir_path: str,
        file_path: str,
        repo: str,
        content: str,
        full_module_path: str
    ) -> CodeEntity:
        """Create a Module entity for a Python module"""
        import os

        file_name = os.path.basename(file_path)
        is_package = file_name == "__init__.py"

        # Get module docstring if available
        docstring = ""
        lines = content.split('\n')
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                # Found potential docstring
                quote = '"""' if stripped.startswith('"""') else "'''"
                if stripped.count(quote) >= 2:
                    # Single line docstring
                    docstring = stripped.strip(quote).strip()
                else:
                    # Multi-line docstring
                    docstring_lines = [stripped.lstrip(quote)]
                    for j in range(i + 1, min(i + 20, len(lines))):
                        if quote in lines[j]:
                            docstring_lines.append(lines[j].split(quote)[0])
                            break
                        docstring_lines.append(lines[j])
                    docstring = " ".join(docstring_lines).strip()
                break
            elif stripped and not stripped.startswith('#'):
                # Non-empty, non-comment line before docstring
                break

        # Truncate docstring
        if len(docstring) > 200:
            docstring = docstring[:200] + "..."

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, module_name, "module"),
            name=module_name,
            entity_type="Module",
            file_path=file_path,
            line_start=1,
            line_end=1,
            language="python",
            repo=repo,
            signature=f"module {full_module_path}" + (" (package)" if is_package else ""),
            docstring=docstring,
            module_name=module_name,
            module_path=full_module_path,
        )

    def _extract_python_import(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str
    ) -> List[CodeEntity]:
        """Extract Python import statement"""
        entities = []

        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    name = source[child.start_byte:child.end_byte].decode()
                    entities.append(CodeEntity(
                        entity_id=generate_entity_id(repo, file_path, f"import:{name}"),
                        name=name,
                        entity_type="Import",
                        file_path=file_path,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        language="python",
                        repo=repo,
                        signature=source[node.start_byte:node.end_byte].decode(),
                    ))

        elif node.type == "import_from_statement":
            module = ""
            for child in node.children:
                if child.type == "dotted_name":
                    module = source[child.start_byte:child.end_byte].decode()
                    break
            if module:
                entities.append(CodeEntity(
                    entity_id=generate_entity_id(repo, file_path, f"import:{module}"),
                    name=module,
                    entity_type="Import",
                    file_path=file_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    language="python",
                    repo=repo,
                    signature=source[node.start_byte:node.end_byte].decode(),
                ))

        return entities

    def _extract_python_class(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str
    ) -> Optional[CodeEntity]:
        """Extract Python class definition"""
        name = ""

        for child in node.children:
            if child.type == "identifier":
                name = source[child.start_byte:child.end_byte].decode()
                break

        if not name:
            return None

        # Get docstring
        docstring = self._get_python_docstring(node, source)

        # Build signature (class line only)
        full_code = source[node.start_byte:node.end_byte].decode()
        sig_lines = full_code.split('\n')
        signature = sig_lines[0] if sig_lines else full_code

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, name, "class"),
            name=name,
            entity_type="Class",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language="python",
            repo=repo,
            signature=signature,
            docstring=docstring,
        )

    def _extract_python_function(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str
    ) -> Optional[CodeEntity]:
        """Extract Python function definition"""
        name = ""
        params = ""
        return_type = ""

        for child in node.children:
            if child.type == "identifier":
                name = source[child.start_byte:child.end_byte].decode()
            elif child.type == "parameters":
                params = source[child.start_byte:child.end_byte].decode()
            elif child.type == "type":
                return_type = source[child.start_byte:child.end_byte].decode()

        if not name:
            return None

        # Get docstring
        docstring = self._get_python_docstring(node, source)

        # Build signature
        full_code = source[node.start_byte:node.end_byte].decode()
        sig_lines = full_code.split('\n')
        signature = sig_lines[0] if sig_lines else full_code

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, name, params),
            name=name,
            entity_type="Function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language="python",
            repo=repo,
            signature=signature,
            params=params,
            return_type=return_type,
            docstring=docstring,
        )

    def _extract_python_calls(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str,
        caller: CodeEntity
    ) -> List[CodeEdge]:
        """Extract function calls from Python function body"""
        edges = []

        for call in self._find_nodes_by_type(node, "call"):
            callee_name = ""

            for child in call.children:
                if child.type == "identifier":
                    callee_name = source[child.start_byte:child.end_byte].decode()
                elif child.type == "attribute":
                    callee_name = source[child.start_byte:child.end_byte].decode()

            if callee_name:
                edge = CodeEdge(
                    edge_id=generate_edge_id(caller.entity_id, callee_name, "CALLS"),
                    from_entity_id=caller.entity_id,
                    to_entity_id=callee_name,
                    edge_type="CALLS",
                    file_path=file_path,
                    line_number=call.start_point[0] + 1,
                )
                edges.append(edge)

        return edges

    def _get_python_docstring(self, node: Node, source: bytes) -> str:
        """Get Python docstring from function/class body"""
        # Find block/body
        body = None
        for child in node.children:
            if child.type == "block":
                body = child
                break

        if not body or not body.children:
            return ""

        # First child of block might be expression_statement with string
        first = body.children[0]
        if first.type == "expression_statement":
            for child in first.children:
                if child.type == "string":
                    docstring = source[child.start_byte:child.end_byte].decode()
                    # Remove quotes
                    return docstring.strip('"""').strip("'''").strip()

        return ""

    # ============= TYPESCRIPT EXTRACTION =============

    def _extract_typescript(
        self,
        content: str,
        file_path: str,
        repo: str,
        language: str
    ) -> Tuple[List[CodeEntity], List[CodeEdge]]:
        """Extract entities and edges from TypeScript/JavaScript code"""
        import os

        entities = []
        edges = []

        parser = self.tsx_parser if language == "tsx" else self.ts_parser
        tree = parser.parse(content.encode())
        root = tree.root_node
        source = content.encode()

        # Calculate TypeScript/JS module from file path
        # e.g., components/Button.tsx -> components/Button
        dir_path = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)

        # Module name is file name without extension
        module_name = os.path.splitext(file_name)[0]
        if module_name == "index":
            # index files represent the directory
            module_name = os.path.basename(dir_path) if dir_path else repo

        # Full module path (using / for JS/TS conventions)
        if dir_path:
            full_module_path = f"{dir_path}/{module_name}"
        else:
            full_module_path = module_name

        # Create Module entity
        module_entity = self._create_ts_module_entity(
            module_name, dir_path, file_path, repo, content, full_module_path, language
        )
        entities.append(module_entity)

        # Create File entity
        file_entity = self._create_file_entity(file_path, repo, language, content, module_name, full_module_path)
        entities.append(file_entity)

        # Create BELONGS_TO edge from File to Module
        belongs_to_edge = CodeEdge(
            edge_id=generate_edge_id(file_entity.entity_id, module_entity.entity_id, "BELONGS_TO"),
            from_entity_id=file_entity.entity_id,
            to_entity_id=module_entity.entity_id,
            edge_type="BELONGS_TO",
            file_path=file_path,
            line_number=1,
        )
        edges.append(belongs_to_edge)

        # Extract imports
        for node in self._find_nodes_by_type(root, "import_statement"):
            entity = self._extract_ts_import(node, source, file_path, repo, language)
            if entity:
                entity.module_name = module_name
                entity.module_path = full_module_path
                entities.append(entity)

        # Extract classes
        for node in self._find_nodes_by_type(root, "class_declaration"):
            entity = self._extract_ts_class(node, source, file_path, repo, language)
            if entity:
                entity.module_name = module_name
                entity.module_path = full_module_path
                entities.append(entity)

        # Extract interfaces
        for node in self._find_nodes_by_type(root, "interface_declaration"):
            entity = self._extract_ts_interface(node, source, file_path, repo, language)
            if entity:
                entity.module_name = module_name
                entity.module_path = full_module_path
                entities.append(entity)

        # Extract functions
        for node in self._find_nodes_by_type(root, "function_declaration"):
            entity = self._extract_ts_function(node, source, file_path, repo, language)
            if entity:
                entity.module_name = module_name
                entity.module_path = full_module_path
                entities.append(entity)

        # Extract arrow functions (const foo = () => {})
        for node in self._find_nodes_by_type(root, "lexical_declaration"):
            entity = self._extract_ts_arrow_function(node, source, file_path, repo, language)
            if entity:
                entity.module_name = module_name
                entity.module_path = full_module_path
                entities.append(entity)

        return entities, edges

    def _create_ts_module_entity(
        self,
        module_name: str,
        dir_path: str,
        file_path: str,
        repo: str,
        content: str,
        full_module_path: str,
        language: str
    ) -> CodeEntity:
        """Create a Module entity for a TypeScript/JavaScript module"""
        import os

        file_name = os.path.basename(file_path)
        is_index = module_name == "index" or file_name.startswith("index.")

        # Try to extract JSDoc comment at top of file
        docstring = ""
        lines = content.split('\n')
        if lines and lines[0].strip().startswith('/**'):
            # Found JSDoc at start
            docstring_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('/**'):
                    docstring_lines.append(stripped.lstrip('/**').strip())
                elif stripped.startswith('*/'):
                    break
                elif stripped.startswith('*'):
                    docstring_lines.append(stripped.lstrip('* ').strip())
                else:
                    docstring_lines.append(stripped)
            docstring = " ".join(docstring_lines).strip()

        # Truncate docstring
        if len(docstring) > 200:
            docstring = docstring[:200] + "..."

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, module_name, "module"),
            name=module_name,
            entity_type="Module",
            file_path=file_path,
            line_start=1,
            line_end=1,
            language=language,
            repo=repo,
            signature=f"module {full_module_path}" + (" (index)" if is_index else ""),
            docstring=docstring,
            module_name=module_name,
            module_path=full_module_path,
        )

    def _extract_ts_import(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str,
        language: str
    ) -> Optional[CodeEntity]:
        """Extract TypeScript import statement"""
        module_path = ""

        for child in self._find_nodes_by_type(node, "string"):
            module_path = source[child.start_byte:child.end_byte].decode().strip('"\'')
            break

        if not module_path:
            return None

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, f"import:{module_path}"),
            name=module_path,
            entity_type="Import",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=language,
            repo=repo,
            signature=source[node.start_byte:node.end_byte].decode(),
        )

    def _extract_ts_class(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str,
        language: str
    ) -> Optional[CodeEntity]:
        """Extract TypeScript class declaration"""
        name = ""

        for child in node.children:
            if child.type == "type_identifier":
                name = source[child.start_byte:child.end_byte].decode()
                break

        if not name:
            return None

        docstring = self._get_ts_docstring(node, source)

        full_code = source[node.start_byte:node.end_byte].decode()
        sig_lines = full_code.split('\n')
        signature = sig_lines[0] if sig_lines else full_code

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, name, "class"),
            name=name,
            entity_type="Class",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=language,
            repo=repo,
            signature=signature,
            docstring=docstring,
        )

    def _extract_ts_interface(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str,
        language: str
    ) -> Optional[CodeEntity]:
        """Extract TypeScript interface declaration"""
        name = ""

        for child in node.children:
            if child.type == "type_identifier":
                name = source[child.start_byte:child.end_byte].decode()
                break

        if not name:
            return None

        docstring = self._get_ts_docstring(node, source)

        full_code = source[node.start_byte:node.end_byte].decode()
        sig_lines = full_code.split('\n')
        signature = sig_lines[0] if sig_lines else full_code

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, name, "interface"),
            name=name,
            entity_type="Interface",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=language,
            repo=repo,
            signature=signature,
            docstring=docstring,
        )

    def _extract_ts_function(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str,
        language: str
    ) -> Optional[CodeEntity]:
        """Extract TypeScript function declaration"""
        name = ""
        params = ""

        for child in node.children:
            if child.type == "identifier":
                name = source[child.start_byte:child.end_byte].decode()
            elif child.type == "formal_parameters":
                params = source[child.start_byte:child.end_byte].decode()

        if not name:
            return None

        docstring = self._get_ts_docstring(node, source)

        full_code = source[node.start_byte:node.end_byte].decode()
        sig_lines = full_code.split('\n')
        signature = sig_lines[0] if sig_lines else full_code

        return CodeEntity(
            entity_id=generate_entity_id(repo, file_path, name, params),
            name=name,
            entity_type="Function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=language,
            repo=repo,
            signature=signature,
            params=params,
            docstring=docstring,
        )

    def _extract_ts_arrow_function(
        self,
        node: Node,
        source: bytes,
        file_path: str,
        repo: str,
        language: str
    ) -> Optional[CodeEntity]:
        """Extract TypeScript arrow function (const foo = () => {})"""
        # Find variable_declarator with arrow_function
        for declarator in self._find_nodes_by_type(node, "variable_declarator"):
            name = ""
            is_arrow = False
            params = ""

            for child in declarator.children:
                if child.type == "identifier":
                    name = source[child.start_byte:child.end_byte].decode()
                elif child.type == "arrow_function":
                    is_arrow = True
                    for arrow_child in child.children:
                        if arrow_child.type == "formal_parameters":
                            params = source[arrow_child.start_byte:arrow_child.end_byte].decode()

            if name and is_arrow:
                docstring = self._get_ts_docstring(node, source)

                full_code = source[node.start_byte:node.end_byte].decode()
                sig_lines = full_code.split('\n')
                signature = sig_lines[0] if sig_lines else full_code

                return CodeEntity(
                    entity_id=generate_entity_id(repo, file_path, name, params),
                    name=name,
                    entity_type="Function",
                    file_path=file_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    language=language,
                    repo=repo,
                    signature=signature,
                    params=params,
                    docstring=docstring,
                )

        return None

    def _get_ts_docstring(self, node: Node, source: bytes) -> str:
        """Get JSDoc comment above TypeScript declaration"""
        prev = node.prev_sibling
        while prev:
            if prev.type == "comment":
                text = source[prev.start_byte:prev.end_byte].decode()
                if text.startswith("/**"):
                    # JSDoc comment
                    return text.strip("/* \n")
                elif text.startswith("//"):
                    return text.lstrip("/ ").strip()
            else:
                break
            prev = prev.prev_sibling

        return ""


# ============= TESTING =============

if __name__ == "__main__":
    # Test the extractor
    extractor = TreeSitterExtractor()

    # Test Go code
    go_code = '''
package module

import (
    "context"
    "encoding/json"

    sdk "github.com/cosmos/cosmos-sdk/types"
)

// Module implements the AppModule interface.
type Module struct {
    key           storetypes.StoreKey
    Keeper        server.Keeper
}

// Keeper handles eco-credit operations
type Keeper struct {
    storeKey sdk.StoreKey
}

// NewModule returns a new Module.
func NewModule(storeKey storetypes.StoreKey) *Module {
    return &Module{key: storeKey}
}

// CreateBatch creates a new credit batch
func (k Keeper) CreateBatch(ctx context.Context, msg *MsgCreateBatch) (*MsgCreateBatchResponse, error) {
    k.validateMsg(msg)
    return nil, nil
}
'''

    print("=== Testing Go Extraction ===")
    entities, edges = extractor.extract("go", go_code, "x/ecocredit/module.go", "regen-ledger")

    print(f"\nExtracted {len(entities)} entities:")
    for e in entities:
        print(f"  - {e.entity_type}: {e.name} (line {e.line_start})")
        if e.docstring:
            print(f"    Docstring: {e.docstring[:50]}...")

    print(f"\nExtracted {len(edges)} CALLS edges:")
    for edge in edges:
        print(f"  - {edge.from_entity_id[:8]}... CALLS {edge.to_entity_id} (line {edge.line_number})")

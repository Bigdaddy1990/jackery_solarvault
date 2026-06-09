__all__ = ["BaseResolver", "Resolver"]  # noqa: D100

import re
import string

from .error import *  # noqa: F403
from .nodes import *  # noqa: F403


class ResolverError(YAMLError):  # noqa: F405
    pass


class BaseResolver:  # noqa: D101
    DEFAULT_SCALAR_TAG = "tag:yaml.org,2002:str"
    DEFAULT_SEQUENCE_TAG = "tag:yaml.org,2002:seq"
    DEFAULT_MAPPING_TAG = "tag:yaml.org,2002:map"

    yaml_implicit_resolvers = {}  # noqa: RUF012
    yaml_path_resolvers = {}  # noqa: RUF012

    def __init__(self) -> None:
        """
        Initialize resolver path stacks for experimental path-based tag resolution.
        
        Creates two empty stacks:
        - resolver_exact_paths: per-depth dicts mapping (path, kind) to resolved tag for exact matches.
        - resolver_prefix_paths: per-depth lists of (path, kind) candidate entries used while traversing.
        """  # noqa: E501
        self.resolver_exact_paths = []
        self.resolver_prefix_paths = []

    @classmethod
    def add_implicit_resolver(cls, tag, regexp, first) -> None:  # noqa: ANN001
        """Register an implicit scalar resolver on the resolver class for the given tag and pattern.

        Ensures the class has its own writable `yaml_implicit_resolvers` mapping before modifying it, then associates the provided `(tag, regexp)` tuple with each initial character in `first`. If `first` is `None`, the resolver is registered under the wildcard key `None`.

        Parameters:
            tag (str): The YAML tag to associate with matches (e.g., 'tag:yaml.org,2002:int').
            regexp (re.Pattern | callable): A compiled regular expression (or regex-like callable) used to test scalar values.
            first (iterable[str] | None): An iterable of characters whose presence as the scalar's first character triggers testing with `regexp`, or `None` to register as a wildcard matcher.
        """  # noqa: E501
        if "yaml_implicit_resolvers" not in cls.__dict__:
            implicit_resolvers = {}
            for key in cls.yaml_implicit_resolvers:
                implicit_resolvers[key] = cls.yaml_implicit_resolvers[key][:]
            cls.yaml_implicit_resolvers = implicit_resolvers
        if first is None:
            first = [None]
        for ch in first:
            cls.yaml_implicit_resolvers.setdefault(ch, []).append((tag, regexp))

    @classmethod
    def add_path_resolver(cls, tag, path, kind=None) -> None:  # noqa: ANN001, PLR0912
        # Note: `add_path_resolver` is experimental.  The API could be changed.
        # `new_path` is a pattern that is matched against the path from the
        # root to the node that is being considered.  `node_path` elements are
        # tuples `(node_check, index_check)`.  `node_check` is a node class:
        # `ScalarNode`, `SequenceNode`, `MappingNode` or `None`.  `None`
        # matches any kind of a node.  `index_check` could be `None`, a boolean
        # value, a string value, or a number.  `None` and `False` match against
        # any _value_ of sequence and mapping nodes.  `True` matches against
        # any _key_ of a mapping node.  A string `index_check` matches against
        # a mapping value that corresponds to a scalar key which content is
        # equal to the `index_check` value.  An integer `index_check` matches
        # against a sequence value with the index equal to `index_check`.
        """Register an experimental path-based resolver that assigns a YAML tag to nodes matching a path pattern.

        Parameters:
            tag (str): The tag to associate with matching node paths.
            path (Iterable): Sequence describing the path pattern from the document root to a target node.
                Each element may be:
                  - a single value (str/int/None) describing an index check for the current level;
                  - a 1-tuple/list [node_check] meaning the node type check with index check = True;
                  - a 2-tuple/list (node_check, index_check) giving both checks.
                Acceptable node_check shortcuts: str → scalar node, list → sequence node, dict → mapping node,
                or one of the node classes ScalarNode, SequenceNode, MappingNode, or None to match any node.
                index_check may be an int, a string, True, False, or None as described by the API.
            kind (optional): Restricts the resolver to a specific node kind; accepts the same shortcuts as node_check
                (str/list/dict → corresponding node classes) or one of ScalarNode/SequenceNode/MappingNode, or None.

        Raises:
            ResolverError: If any path element, node_check, index_check, or kind is not one of the allowed forms.

        Notes:
            - This API is experimental and may change.
            - The provided pattern is normalized to an internal sequence of (node_check, index_check) pairs and stored
              on the class-level registry so it is applied during resolution.
        """  # noqa: E501
        if "yaml_path_resolvers" not in cls.__dict__:
            cls.yaml_path_resolvers = cls.yaml_path_resolvers.copy()
        new_path = []
        for element in path:
            if isinstance(element, (list, tuple)):
                if len(element) == 2:  # noqa: PLR2004
                    node_check, index_check = element
                elif len(element) == 1:
                    node_check = element[0]
                    index_check = True
                else:
                    raise ResolverError(f"Invalid path element: {element}")  # noqa: TRY003
            else:
                node_check = None
                index_check = element
            if node_check is str:
                node_check = ScalarNode  # noqa: F405
            elif node_check is list:
                node_check = SequenceNode  # noqa: F405
            elif node_check is dict:
                node_check = MappingNode  # noqa: F405
            elif (
                node_check not in {ScalarNode, SequenceNode, MappingNode}  # noqa: F405
                and not isinstance(node_check, str)
                and node_check is not None
            ):
                raise ResolverError(f"Invalid node checker: {node_check}")  # noqa: TRY003
            if not isinstance(index_check, (str, int)) and index_check is not None:
                raise ResolverError(f"Invalid index checker: {index_check}")  # noqa: TRY003
            new_path.append((node_check, index_check))
        if kind is str:
            kind = ScalarNode  # noqa: F405
        elif kind is list:
            kind = SequenceNode  # noqa: F405
        elif kind is dict:
            kind = MappingNode  # noqa: F405
        elif kind not in {ScalarNode, SequenceNode, MappingNode} and kind is not None:  # noqa: F405
            raise ResolverError(f"Invalid node kind: {kind}")  # noqa: TRY003
        cls.yaml_path_resolvers[tuple(new_path), kind] = tag

    def descend_resolver(self, current_node, current_index) -> None:  # noqa: ANN001
        """Update resolver stacks for a new traversal depth based on the current node and index.

        Pushes a new frame of exact and prefix path matches onto the resolver stacks so subsequent
        calls to `resolve` can consult path-based tag mappings for the current traversal position.

        Parameters:
            current_node: The node being entered at this depth, or `None` when entering the root/empty context.
            current_index: The index or key within the parent for `current_node` (an integer, a `ScalarNode` used as a key, or `None`).
        """  # noqa: E501
        if not self.yaml_path_resolvers:
            return
        exact_paths = {}
        prefix_paths = []
        if current_node:
            depth = len(self.resolver_prefix_paths)
            for path, kind in self.resolver_prefix_paths[-1]:
                if self.check_resolver_prefix(
                    depth, path, kind, current_node, current_index
                ):
                    if len(path) > depth:
                        prefix_paths.append((path, kind))
                    else:
                        exact_paths[kind] = self.yaml_path_resolvers[path, kind]
        else:
            for path, kind in self.yaml_path_resolvers:
                if not path:
                    exact_paths[kind] = self.yaml_path_resolvers[path, kind]
                else:
                    prefix_paths.append((path, kind))
        self.resolver_exact_paths.append(exact_paths)
        self.resolver_prefix_paths.append(prefix_paths)

    def ascend_resolver(self) -> None:
        """Restore resolver state by removing the most-recent exact-path and prefix-path frames.

        If no path-based resolvers are registered, this method does nothing. Otherwise it pops the top frames from the instance stacks `resolver_exact_paths` and `resolver_prefix_paths`.
        """  # noqa: E501
        if not self.yaml_path_resolvers:
            return
        self.resolver_exact_paths.pop()
        self.resolver_prefix_paths.pop()

    def check_resolver_prefix(  # noqa: PLR0911, PLR6301
        self,
        depth,  # noqa: ANN001
        path,  # noqa: ANN001
        kind,  # noqa: ANN001
        current_node,  # noqa: ANN001
        current_index,  # noqa: ANN001
    ) -> bool | None:
        """Determine whether the resolver path element at the specified depth matches the current traversal node and index.

        Parameters:
                depth (int): 1-based traversal depth used to select the path element to check.
                path (Sequence[Tuple[Any, Any]]): Compiled resolver path where each element is (node_check, index_check).
                kind (type | None): Expected node kind for the overall resolver (may be None); used by callers to categorize matches.
                current_node (Node): The node object at the current traversal position.
                current_index (int | ScalarNode | None): The current mapping key, sequence index, or `None` when not applicable.

        Returns:
                True if the node and index satisfy the path element's checks, `None` otherwise.
        """  # noqa: D206, E101, E501
        node_check, index_check = path[depth - 1]
        if isinstance(node_check, str):
            if current_node.tag != node_check:
                return None
        elif node_check is not None:  # noqa: SIM102
            if not isinstance(current_node, node_check):
                return None
        if index_check is True and current_index is not None:
            return None
        if (index_check is False or index_check is None) and current_index is None:
            return None
        if isinstance(index_check, str):
            if not (
                isinstance(current_index, ScalarNode)  # noqa: F405
                and index_check == current_index.value
            ):
                return None
        elif isinstance(index_check, int) and not isinstance(index_check, bool):  # noqa: SIM102
            if index_check != current_index:
                return None
        return True

    def resolve(self, kind, value, implicit):  # noqa: ANN001, ANN201, PLR0911
        """Determine the YAML tag for a node based on implicit scalar patterns, registered path resolvers, or kind-based defaults.

        Parameters:
            kind: The node type class (e.g., ScalarNode, SequenceNode, MappingNode).
            value (str): The scalar value to test for implicit resolution (ignored for non-scalars).
            implicit: A two-element sequence controlling implicit scalar resolution where the first element
                enables implicit tag matching and the second element is used if the first is not applied.

        Returns:
            The resolved tag (string) when a match is found or a default tag for the node kind; `None` if
            no resolver or default applies.
        """  # noqa: E501
        if kind is ScalarNode and implicit[0]:  # noqa: F405
            if value == "":  # noqa: PLC1901
                resolvers = self.yaml_implicit_resolvers.get("", [])
            else:
                resolvers = self.yaml_implicit_resolvers.get(value[0], [])
            wildcard_resolvers = self.yaml_implicit_resolvers.get(None, [])
            for tag, regexp in resolvers + wildcard_resolvers:
                if regexp.match(value):
                    return tag
            implicit = implicit[1]
        if self.yaml_path_resolvers:
            exact_paths = self.resolver_exact_paths[-1]
            if kind in exact_paths:
                return exact_paths[kind]
            if None in exact_paths:
                return exact_paths[None]
        if kind is ScalarNode:  # noqa: F405
            return self.DEFAULT_SCALAR_TAG
        if kind is SequenceNode:  # noqa: F405
            return self.DEFAULT_SEQUENCE_TAG
        if kind is MappingNode:  # noqa: F405
            return self.DEFAULT_MAPPING_TAG
        return None


class Resolver(BaseResolver):  # noqa: D101
    pass


Resolver.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(
        r"""^(?:yes|Yes|YES|no|No|NO
                    |true|True|TRUE|false|False|FALSE
                    |on|On|ON|off|Off|OFF)$""",
        re.VERBOSE,
    ),
    list("yYnNtTfFoO"),
)

Resolver.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
                    |\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
                    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                    |[-+]?\.(?:inf|Inf|INF)
                    |\.(?:nan|NaN|NAN))$""",
        re.VERBOSE,
    ),
    list("-+0123456789."),
)

Resolver.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(
        r"""^(?:[-+]?0b[0-1_]+
                    |[-+]?0[0-7_]+
                    |[-+]?(?:0|[1-9][0-9_]*)
                    |[-+]?0x[0-9a-fA-F_]+
                    |[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$""",
        re.VERBOSE,
    ),
    list("-+0123456789"),
)

Resolver.add_implicit_resolver(
    "tag:yaml.org,2002:merge", re.compile(r"^(?:<<)$"), ["<"]
)

Resolver.add_implicit_resolver(
    "tag:yaml.org,2002:null",
    re.compile(
        r"""^(?: ~
                    |null|Null|NULL
                    | )$""",
        re.VERBOSE,
    ),
    ["~", "n", "N", ""],
)

Resolver.add_implicit_resolver(
    "tag:yaml.org,2002:timestamp",
    re.compile(
        r"""^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]
                    |[0-9][0-9][0-9][0-9] -[0-9][0-9]? -[0-9][0-9]?
                     (?:[Tt]|[ \t]+)[0-9][0-9]?
                     :[0-9][0-9] :[0-9][0-9] (?:\.[0-9]*)?
                     (?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$""",
        re.VERBOSE,
    ),
    list(string.digits),
)

Resolver.add_implicit_resolver("tag:yaml.org,2002:value", re.compile(r"^(?:=)$"), ["="])

# The following resolver is only for documentation purposes. It cannot work
# because plain scalars cannot start with '!', '&', or '*'.
Resolver.add_implicit_resolver(
    "tag:yaml.org,2002:yaml", re.compile(r"^(?:!|&|\*)$"), list("!&*")
)

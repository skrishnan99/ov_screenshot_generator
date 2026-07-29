"""Template module contract.

Every slide template is its own Python module under
``deck_builder/templates/``, exactly mirroring the convention in
``recipe_decryption/case_study/templates``. A template module must
expose five attributes:

* ``TEMPLATE_ID: str`` — unique id, used in ``SlideSpec.template_id``.
* ``SKELETON_PATH: Path`` — the ``.pptx`` skeleton under
  ``deck_builder/skeletons/`` (same files as recipe_decryption).
* ``HOLE_SCHEMA: list[HoleSpec]`` — one entry per ``{{token}}`` text
  hole / picture-alt-text hole in the skeleton.
* ``applies(bundle: RunBundle) -> list[TemplateContext]`` — contexts
  this template emits slides for. Hardcoded/global templates return
  ``[None]`` (or ``[]`` to suppress). Per-model templates return the
  matching ``ModelInfo`` objects.
* ``build(bundle, ctx, llm_cache) -> dict[str, HoleValue]`` — initial
  hole values for one slide.

The only signature difference from recipe_decryption is the input
object: a ``RunBundle`` (screenshots + descriptions) instead of a
``LoadedRecipe`` (raw recipe export), and ``build`` instead of
``from_recipe`` to make that difference explicit at call sites.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from deck_builder.errors import TemplateContractError

if TYPE_CHECKING:
    from deck_builder.manifest import HoleSpec, HoleValue
    from deck_builder.run_bundle import RunBundle

# None for recipe-global slides; ModelInfo for per-model slides.
TemplateContext = Any


@runtime_checkable
class SlideTemplateModule(Protocol):
    TEMPLATE_ID: str
    SKELETON_PATH: Path
    HOLE_SCHEMA: "list[HoleSpec]"

    applies: Callable[["RunBundle"], list[TemplateContext]]
    build: Callable[
        ["RunBundle", TemplateContext, dict[str, Any]],
        "dict[str, HoleValue]",
    ]


def validate_template_module(module: Any) -> None:
    """Raise ``TemplateContractError`` if ``module`` breaks the contract.

    Same shallow checks as recipe_decryption: attribute presence and
    types, plus duplicate hole names (two ``HoleSpec``s sharing a name
    is always a bug the renderer would silently paper over).
    """
    name = getattr(module, "__name__", repr(module))

    if not isinstance(getattr(module, "TEMPLATE_ID", None), str) or not module.TEMPLATE_ID:
        raise TemplateContractError(
            f"Template module {name}: TEMPLATE_ID must be a non-empty str"
        )

    skeleton = getattr(module, "SKELETON_PATH", None)
    if not isinstance(skeleton, Path):
        raise TemplateContractError(
            f"Template module {name}: SKELETON_PATH must be a pathlib.Path, "
            f"got {type(skeleton).__name__}"
        )
    if not skeleton.exists():
        raise TemplateContractError(
            f"Template module {name}: skeleton file missing: {skeleton}"
        )

    schema = getattr(module, "HOLE_SCHEMA", None)
    if not isinstance(schema, list):
        raise TemplateContractError(
            f"Template module {name}: HOLE_SCHEMA must be a list, "
            f"got {type(schema).__name__}"
        )
    seen: set[str] = set()
    for spec in schema:
        spec_name = getattr(spec, "name", None)
        if not isinstance(spec_name, str) or not spec_name:
            raise TemplateContractError(
                f"Template module {name}: HOLE_SCHEMA entry missing a string name"
            )
        if spec_name in seen:
            raise TemplateContractError(
                f"Template module {name}: duplicate hole name {spec_name!r} in HOLE_SCHEMA"
            )
        seen.add(spec_name)

    for fn_name in ("applies", "build"):
        if not callable(getattr(module, fn_name, None)):
            raise TemplateContractError(
                f"Template module {name}: {fn_name!r} is not callable"
            )

# Changelog

Cada versão vira uma Release no GitHub com o texto da sua seção. Para publicar: atualizar `version` em `pyops/blender_manifest.toml` (e `bl_info`), escrever a seção abaixo, commitar e criar a tag `vX.Y.Z`.

## [1.7.0] - 2026-10-24

Primeira versão publicada como Blender Extension.

### Adicionado
- Distribute: saída `IfcAnnotation` para desenhos (Bonsai).
- Operador EnergyPlus (Honeybee) e ajustes em Radiance e `type_geometry`.
- `blender_manifest.toml`: instalação e atualização pelo repositório de extensões.

### Alterado
- Operador `panel` melhorado.
- Documentação e contexto reorganizados (`.claude/`, `docs/`).

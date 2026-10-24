# Changelog

Cada versão vira uma Release no GitHub com o texto da sua seção. Para publicar: atualizar `version` em `pyops/blender_manifest.toml` (e `bl_info`), escrever a seção abaixo, commitar e criar a tag `vX.Y.Z`.

## [1.7.1] - 2026-10-24

### Corrigido
- Instalação como extensão acusava "Policy violation with top level module": o addon registrava um módulo de topo em `sys.modules`. Os arquivos de `ops/` agora recebem `pyops_api` direto no namespace; para scripts e MCP, `bpy.app.driver_namespace["pyops_api"]`.

### Alterado
- Todo o prefixo `refops` virou `pyops` (classes, operadores, logs, atributos). A pasta padrão das bibliotecas passa a ser `C:\pyops_libs`: renomeie a antiga ou reinstale. Atributos com nome antigo em arquivos `.blend`/`.ifc` existentes não casam mais.
- Operadores de `ops/` não fazem mais `import pyops_api`.

## [1.7.0] - 2026-10-24

Primeira versão publicada como Blender Extension.

### Adicionado
- Distribute: saída `IfcAnnotation` para desenhos (Bonsai).
- Operador EnergyPlus (Honeybee) e ajustes em Radiance e `type_geometry`.
- `blender_manifest.toml`: instalação e atualização pelo repositório de extensões.

### Alterado
- Operador `panel` melhorado.
- Documentação e contexto reorganizados (`.claude/`, `docs/`).

# PyOps

Addon de Blender que gera elementos IFC reais (paredes, lajes, pilares, vigas, painéis, aberturas, espaços) a partir de atributos pintados numa malha de referência, usando Bonsai BIM e IfcOpenShell. Cada script `.py` de uma pasta vira uma operação na interface, e o fluxo é não-destrutivo: a malha pai é duplicada e a operação roda sobre a cópia. O mesmo mecanismo de atributos alimenta análises climáticas e de iluminação com Ladybug, Honeybee e Radiance.

## Links

- **Instalar o addon (atualiza sozinho):** no Blender 4.2+, *Preferences → Get Extensions → Repositories → + → Add Remote Repository* e cole `https://vilarinhostudio.github.io/pyops/index.json`. Depois instale **PyOps** pela lista.
- **Instalar o addon (manual):** [pyops.zip](https://github.com/vilarinhostudio/pyops/releases/latest/download/pyops.zip), em *Get Extensions → Install from Disk*.
- **Operações (`ops/`):** [ops.zip](https://github.com/vilarinhostudio/pyops/releases/latest/download/ops.zip). Descompacte e aponte *Root Directory*, nas preferências do addon, para a pasta `ops/`.
- **Estilos de desenho (`drawing-bonsai/`):** [drawing-bonsai.zip](https://github.com/vilarinhostudio/pyops/releases/latest/download/drawing-bonsai.zip), com hachuras e temas SVG/CSS para plantas e cortes do Bonsai.
- **Novidades de cada versão:** [Releases](https://github.com/vilarinhostudio/pyops/releases) e [CHANGELOG.md](CHANGELOG.md).
- **Documentação:** [docs/](docs/).

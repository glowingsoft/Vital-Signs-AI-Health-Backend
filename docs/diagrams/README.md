# Diagrams for docs/App-Code-Walkthrough.docx

Each `figNN-*.mmd` is the Mermaid source of Figure NN in the walkthrough; the `.png` next to it is what the
document embeds. `mermaid-config.json` keeps the spacing compact so the text stays readable on a Word page.

Re-render one diagram after editing it (Mermaid CLI 11.4.2):

```sh
npx -p @mermaid-js/mermaid-cli@11.4.2 mmdc -i fig09-triage-first.mmd -o fig09-triage-first.png \
  -w 1600 -s 2 -b white -c mermaid-config.json
```

Use `-w 760` instead of `-w 1600` for the timelines (fig11 to fig13).
Keep a diagram within about 1,100 x 1,500 px (before `-s 2`), or its text gets too small on the page.

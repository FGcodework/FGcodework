# FG profile generator

The profile README and its SVG assets are generated from one file, `profile.json`.
Edit that file; a GitHub Actions workflow rebuilds `README.md` and `assets/*.svg`
and commits them. Do not edit the generated files by hand, they are overwritten.

```text
FGcodework/
├── profile.json                  <- the only file you normally edit
├── README.md                     <- generated
├── assets/
│   ├── *.svg                     <- generated
│   └── src/brand-tile.png        <- source image for the hero logo (edit/replace freely)
├── generator/
│   ├── build.py
│   └── requirements.txt          (Pillow)
└── .github/workflows/build.yml
```

## Workflow

`Build profile` runs when `profile.json`, `generator/**` or `assets/src/**` change,
on demand (Actions → Build profile → Run workflow) and every Monday (refreshes
extension logos). It runs `check` first, then builds, then commits only if something
changed. Commits made by the workflow do not trigger it again.

Locally (Python 3.10+ with Pillow):

```bash
python -m pip install -r generator/requirements.txt
python generator/build.py check     # validate, write nothing
python generator/build.py           # build README.md + assets/
```

`check` fails (also in CI) on invalid JSON, an extension without a logo and on text
that would probably overflow its box. Nothing is committed in that case.

## profile.json

Top level keys: `user`, `sections`, `theme`, `hero`, `about`, `featured`, `stack`,
`principle`, `stats`, `contacts`, `footer`, `views`. Keys starting with `$` are ignored.

| Key | What it does |
| --- | --- |
| `sections` | Order of blocks in the README. Remove a name to hide the block. Names: `hero`, `about`, `featured`, `stack`, `principle`, `stats`, `contacts`, `footer`, `views`. |
| `theme` | Colours (hex). `accent` is the blue, `accent2` the coral; the rest are panels, borders and text shades. |
| `hero` | `title`, `tagline`, `subtitle`, `link`, `chips` (list of `{text, style}`), `logo` (path to the brand tile), `side_logos` (up to 4 repo names whose logo is shown on the right). |
| `about` | `label`, `name`, `text`, `side` (up to 2 `{label, value}` pairs). |
| `featured` | `title`, `columns` (1-4) and `items`. Rows are balanced automatically, e.g. 4 items with 3 columns become 2+2, a single item is full width. |
| `stack` | `rows` of `{label, items[]}`. Pills wrap automatically. |
| `principle` | `title`, `text`. |
| `stats` | `rows`: list of rows, each a list of widgets `stats`, `langs`, `streak`. Also `langs_count`, `hide_rank`, `streak_height`. |
| `contacts` | `columns` and `items` of `{id, icon, label, text, url, style}`. Icons: `web`, `github`, `kofi`, `link`. `style: "accent2"` makes it coral. |
| `footer` | Small centred text. |
| `views` | `enabled`, `label`, optional `base` (starting number). |

### A featured item

```json
{
  "repo": "plg_content_fgautolightbox",
  "kind": "plugin",
  "title": "AutoLightbox",
  "alt": "FG AutoLightbox",
  "description": "Turns every image in your articles into a lightbox gallery.",
  "badges": [{ "text": "Joomla 6" }, { "text": "Released", "style": "accent2" }]
}
```

* The logo is fetched from the repository: `assets/logo.webp`, falling back to
  `assets/logo.png`, on `master` then `main`. Override with `"branch": "main"`,
  `"owner"`, or `"logo"` (a path in this repo or a URL).
* `description` is wrapped automatically. Pass a list of strings to control the line breaks.
* The link goes to `https://github.com/<user>/<repo>` unless `"url"` is set.
* `id` (optional) sets the file name, `assets/card-<id>.svg`; the default comes from the title.

## Notes

* The statistics, languages, streak and views widgets are images served by third-party
  services (github-readme-stats, streak-stats, komarev). They are not generated here.
* GitHub disables scheduled workflows in a repository after 60 days without activity.
  Only the weekly logo refresh depends on that; pushes and manual runs always work.

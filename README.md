# Spell Custom Editor
A tool for editing CipSoft client spell records, icons, and preview data.

## Requirements
- Python

## How to Use
After installing Python, run:

```bash
python -m grm
```

Run this command in the directory where the `grm` folder is located.

When the app opens, the first step is to select the client folder.  
Important: select the root client directory (the one that contains folders like `bin`, `assets`, `cache`, etc.), not only `bin` or `assets`.

Then click **Load Client**.  
When loading is complete, everything is ready to edit:
- spell icons
- spell records
- spell previews

## Grid Editing Basics (Preview Editor)
To add an item in the preview editor:
1. Select a spell (or create one).
2. Select an **Effect**, **Missile**, or **Object**.
3. Left-click on the grid to add.

To remove an added item:
- Hold `Ctrl` + left-click on the grid cell.

Extra controls:
- Right-click opens contextual options on the grid.
- Use **Undo** and **Redo** buttons to revert/reapply changes.

## Icon Editor
To modify/remove an icon, select the desired icon in the grid and remove or replace it.

To replace:
1. Select a PNG file first.
2. Click **Add/Replace**.

Behavior:
- If no index is provided, a new index is added.
- If an existing index is provided, that icon is replaced by the selected PNG.

Notes:
- PNG images can be any size; they are resized automatically on import.
- You can create custom indexes to reserve space for future spell icon updates.

## Spells Editor
Usage is straightforward:
- Select an existing spell, or create a new one, or duplicate an existing spell.
- Edit the fields shown in the editor.

The fields are clearly labeled, so editing each spell detail is simple.

## Spell Preview Editor
This is a more advanced feature used to create spell previews in the CipSoft client.

You can:
- Create a new preview record
- Duplicate an existing one
- Edit in the structural/code window
- Edit in the visual grid editor

Note: this feature is still under development and may have some issues.

## Screenshots

### Icon Editor
![Icon Editor](screenshots/icon_editor.png)

### Spells Editor
![Spells Editor](screenshots/spell_editor.png)

### Spell Preview Editor (Structural)
![Spell Preview Editor - Structural](screenshots/spell_preview_editor.png)

### Spell Preview Editor (Grid FX/Missiles)
![Spell Preview Editor - Grid FX/Missiles](screenshots/grid_editor.png)

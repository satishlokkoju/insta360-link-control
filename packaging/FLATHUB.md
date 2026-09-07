# Submitting to Flathub

The app is packaged and passes the linter locally. Publishing is a pull request
to Flathub, reviewed by a human. These are the exact steps.

## Before you start

You need a GitHub account (this uses `satishlokkoju`) and the `gh` CLI, already
authenticated.

## 1. Fork the Flathub submission repository

```bash
gh repo fork flathub/flathub --clone=false
```

## 2. Create a branch named exactly after the app ID

Flathub requires the branch name to match the application ID.

```bash
git clone https://github.com/satishlokkoju/flathub.git ~/flathub-submission
cd ~/flathub-submission
git checkout -b io.github.satishlokkoju.insta360_link_control
```

## 3. Add the manifest at the repository root

```bash
cp /path/to/insta360_link_control/packaging/io.github.satishlokkoju.insta360_link_control.yaml .
git add io.github.satishlokkoju.insta360_link_control.yaml
git commit -m "Add io.github.satishlokkoju.insta360_link_control"
git push -u origin io.github.satishlokkoju.insta360_link_control
```

## 4. Open the pull request against `new-pr`

New submissions target the `new-pr` branch, not `master`.

```bash
gh pr create --repo flathub/flathub --base new-pr \
  --head satishlokkoju:io.github.satishlokkoju.insta360_link_control \
  --title "Add io.github.satishlokkoju.insta360_link_control" \
  --body-file packaging/FLATHUB_PR_BODY.md
```

## 5. What the reviewer will ask about

**`--device=all`.** This is the permission most likely to draw a question, and the
PR body explains it: the camera is driven through `ioctl` calls on `/dev/video*`,
including `UVCIOC_CTRL_QUERY` for the extension units that control the gimbal and
the AI framing modes. Flatpak has no narrower permission for video devices —
`--device=all` is the only option that exposes them. `org.thonny.Thonny` uses the
same permission for serial and USB access.

**Runtime version.** The manifest pins 24.08 rather than the newest runtime. The
reason is in a comment: `tkinter-standalone` vendors a copy of `_tkinter.c` that
calls `Py_GetProgramName`, which was removed in Python 3.14, so the 26.08 runtime
fails to compile. 24.08 ships Python 3.12.

**Trademark.** The app is named "Link Control" and uses an original icon. The
Insta360 name appears only in the summary and description, to say which hardware
it works with, alongside an explicit statement that the project is unaffiliated.

## After it is merged

Flathub builds and publishes automatically. Updates are pull requests to the
app's own repository at `flathub/io.github.satishlokkoju.insta360_link_control`;
bump the `tag` and `commit` in the manifest and add a `<release>` entry to the
metainfo file.

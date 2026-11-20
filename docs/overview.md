# Cairn, in plain words

## What it is

When several people each use an AI assistant to work on the same project, every assistant starts from scratch. It reads the same code again, finds the same problems again, and checks things someone else already checked.

Cairn gives those assistants a shared notebook. Whatever one assistant learns about the project, the others get to see, so nobody's assistant has to work it out twice.

## What it's for

- **A head start.** When an assistant starts work, and again when it opens a part of the project for the first time, it is shown a few short lines on what others already know about that part. It doesn't have to read everything again.
- **No double work.** Before starting a job, an assistant checks whether someone is already doing it or has already done it.
- **Notes that stay accurate.** Each note remembers which version of the code it describes. When that code changes, the note is marked as out of date.
- **Lower cost.** Assistants that read less cost less to run.
- **Proof that it helps.** Cairn leaves out the shared notes for a few sessions on purpose, so you can compare and see whether it makes a difference.

## Which assistants it works with

Cairn works with Claude Code and Codex, where the notes arrive on their own. OpenCode and Pi are supported but still being tested. Any other assistant can use it too: it just has to be told to ask for the notes.

## How to set it up

There are two places on GitHub. The **Cairn kit** is where Cairn itself lives, at `github.com/macladson/cairn`; everyone installs from there. The **shared notes** live in a private repository that only your team can see. Nothing is ever added to the project itself.

These steps are for a public project, where the notes need a private home of their own. If your project is already private, the notes can live in the project's own repository; the kit's README covers that shorter setup.

Setup has two parts: one person creates the shared notes once, then each teammate connects to them.

**Before you start, you need:**

- A GitHub account. The notes can belong to your own account or to an organisation; either works.
- Git (the tool that copies code to and from GitHub) and Python 3 on your computer. Most Macs and Linux computers already have both.
- Optional but handy: the GitHub command-line tool, `gh`, signed in. The quick-way scripts use it to create and protect the notes repository for you.

The commands below are typed into a terminal, the window where you type commands. Replace `macladson/cairn` with the kit's address on GitHub, `<you>` with the account or organisation that will own the notes, `<project>` with the project's name, `<upstream>/<project>` with its address on GitHub, and `<your-github-name>` with your GitHub username.

You also need a **profile** for the project: a folder that names its parts, the kinds of review that matter for it, and its rules for assistants. Copy the kit's `profiles/example` folder and edit it, or ask whoever knows the project best to write one. Below, `<profile folder>` is where yours is.

### Part 1: one person, once (about 15 minutes)

> **The quick way.** After step 1, one command does steps 2 to 7, skips anything already done, and tells you if something needs doing by hand:
>
> ```sh
> ~/cairn/scripts/setup-notes.sh <you>/<project>-notes --project <your project folder> --profile <profile folder>
> ```

**1. Install Cairn.** This downloads the kit and puts the `cairn` command on your computer.

```sh
git clone https://github.com/macladson/cairn.git ~/cairn
~/cairn/install.sh
```

If it prints a note that a folder "is not on your PATH", your computer can't find the command yet. Add that folder to your PATH, then open a new terminal.

**2. Create a private space for the notes.** On github.com, create a new private repository named `<project>-notes`, under your own account or your organisation. Then let your teammates change it: on a personal account, add them as collaborators; in an organisation, give them write access. You can also create it with this command:

```sh
gh repo create <you>/<project>-notes --private
```

**3. Add the daily tidy-up job.** This puts one file in the notes repository. It is all the repository needs: each day the job downloads Cairn from the kit by itself. The file goes on a branch of its own, called `cairn/job`, so it never gets mixed up with the project's code. Do this step before the next one: the first branch uploaded becomes the repository's default branch, and GitHub only runs daily jobs from the default branch.

```sh
git clone https://github.com/<you>/<project>-notes.git
cd <project>-notes
mkdir -p .github/workflows
cp ~/cairn/ci/cairn-daily.yml .github/workflows/
git add -A
git commit -m "Add the Cairn daily job"
git push origin HEAD:cairn/job
cd ..
```

**4. Copy the project's two main versions into it.** The daily job compares the notes with the latest code. It needs a copy of the branch where development happens and the branch that holds the released version; below they are `<dev>` and `<release>`. Nothing is sent to the project.

```sh
git clone https://github.com/<upstream>/<project>.git
cd <project>
git remote add cairn https://github.com/<you>/<project>-notes.git
git push cairn refs/remotes/origin/<dev>:refs/heads/<dev> refs/remotes/origin/<release>:refs/heads/<release>
```

If the project runs automatic jobs of its own on GitHub (tests, builds), this step starts them in the notes repository too, where they are not wanted and use up your allowance. On github.com, open the notes repository's Actions tab, cancel anything that is running, and for each job in the list except cairn-daily choose "Disable workflow" from its "..." menu. The quick-way script does this for you.

**5. Create the shared notebook.** Still inside the project's folder, run:

```sh
git config cairn.remote cairn
cairn init --profile <profile folder>
cairn map check
```

- The first line tells Cairn where the shared notes are kept.
- The second creates the notebook, already set up for the project. That covers the names of its parts, the kinds of review (such as security and performance) and the project's rules for assistants.
- The last checks that every file in the project belongs to one of those parts. It should end with "0 of N files are in no area", where N is the number of files.

**6. Protect the notes from being erased (paid GitHub plans only).** This stops anyone from wiping or rewriting the notes by mistake, but GitHub only offers it for private repositories on a paid plan (Pro, Team or Enterprise). On github.com, open the repository and go to Settings, then Rules, then Rulesets. Add a branch ruleset for `cairn/ledger`, which is where the notes are kept, and turn on "Restrict deletions" and "Block force pushes".

On a free plan, skip this step. The daily job still reports any note that was changed or deleted, and every teammate's computer holds a copy of the notes to restore from.

**7. Start the daily tidy-up job.** On github.com, open the notes repository's Actions tab, choose cairn-daily and press "Run workflow". If GitHub asks you to enable Actions, say yes.

- After this first run, the job runs by itself once a day. It takes about 6 minutes, well within GitHub's free monthly allowance.
- When it finishes you'll see a green tick, and a summary page appears on the `cairn/ledger` branch.
- If it fails with a permissions error, go to Settings, then Actions, then General, and set workflow permissions to "Read and write".

### Part 2: each person, once (about 5 minutes)

> **The quick way.** After step 1, one command does steps 2 and 3. Name the assistants you use (claude-code, codex, opencode or pi):
>
> ```sh
> ~/cairn/scripts/join.sh <you>/<project>-notes --project <your project folder> claude-code
> ```
>
> It also finds the official project's link for you if your copy comes from a fork.

**1. Install Cairn.**

```sh
git clone https://github.com/macladson/cairn.git ~/cairn
~/cairn/install.sh
```

**2. Connect your copy of the project to the shared notes.** Go into the folder where you keep the project. If you don't have it yet, download it first with `git clone https://github.com/<upstream>/<project>.git`. Then run:

```sh
git remote add cairn https://github.com/<you>/<project>-notes.git
git config cairn.remote cairn
cairn whoami --user <your-github-name>
```

The last line tells Cairn who you are, so everything your assistant records carries your name.

Your copy of the project may come from your own GitHub copy of it (a fork) rather than straight from the official one. In that case, also tell Cairn which link points at the official project. That link is usually called `upstream`:

```sh
git config cairn.mainRemote upstream
```

**3. Add Cairn to your assistant.** Do this for each assistant you use.

| Assistant | What to do |
| --- | --- |
| Claude Code | Run `~/cairn/install.sh claude-code`. If it can't install the plugin itself, it prints the two commands to run |
| Codex | Run `~/cairn/install.sh codex`, then type `/hooks` in Codex and approve Cairn. If you already have your own Codex hooks file, the script tells you to copy Cairn's entries into it instead |
| OpenCode | Run `~/cairn/install.sh opencode`, then add the setting described in the kit's `adapters/opencode/README.md` |
| Pi | Run `~/cairn/install.sh pi`, then type `/reload` in Pi |
| Any other assistant | Copy the text from the kit's `adapters/agents-md/AGENTS-snippet.md` into the assistant's instructions file (often called AGENTS.md) |

**4. Check that it works.** In your project folder:

- `cairn brief` lists what is in the shared notes. Right after setup it is nearly empty.
- `cairn guide` shows the project's rules for assistants.
- Start your assistant in the project folder and ask it "What does Cairn tell you about this project?" It should describe the shared notes.

That's it. From now on, work as usual: your assistant gets the shared notes on its own and adds what it learns.

**Keeping Cairn up to date.** When a new version of the kit comes out, run `git -C ~/cairn pull` and then `~/cairn/install.sh claude-code` (or your assistant's name) again. The daily job always uses the latest kit by itself.

### Part 3: turn on the comparison (recommended for a trial)

To find out whether Cairn really helps, one person runs this once in their project folder:

```sh
cairn settings set context_holdout 0.2
```

From then on, one session in five gets no shared notes. After about two weeks, everyone runs `cairn metrics --publish`. Then anyone can run `cairn metrics --team` to see how much assistants read with the notes and without them.

## How to tell if it's working

After about two weeks, Cairn can show how much each assistant read with the shared notes and without them. If assistants with the notes don't read less or give better answers, Cairn isn't worth keeping.

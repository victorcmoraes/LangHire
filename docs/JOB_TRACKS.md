# Local job tracks

Discovery, triage and batch requalification read `job_tracks` from the data
directory's `settings.json`. Missing fields use the original engineering filters.
These title rules do not change location, evidence, salary, duplicate or submission
checks. Invalid configuration stops the operation.

Example for all title families while retaining the existing senior role exclusions:

```json
{
  "job_tracks": {
    "include": ".*",
    "exclude": "\\b(?:staff|principal|manager|diretor|director|head|architect|arquiteto|master(?!\\s+data)|lead|leader)\\b",
    "allow_mobile": true,
    "allow_pure_frontend": true
  }
}
```

Merge this object into existing settings. `include` and `exclude` are nonempty
Python regular expressions, matched without case against normalized titles.
Use `(?!)` for an expression that matches no title. The two boolean options
control additional mobile and pure frontend filters; the exclusion expression
still applies first. Defaults keep both options false and exclude QA, mobile and
ABAP titles as before. Default expressions live in `backend/core/job_tracks.py`.

After editing, run `.venv\Scripts\python.exe -m cli.external triage` and inspect
the local triage report. This command does not collect jobs or send applications.

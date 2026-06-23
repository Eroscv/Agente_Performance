## Description: <br>
Google Sheets API integration with managed OAuth for reading and writing spreadsheet data, creating sheets, applying formatting, and managing ranges. <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[byungkyu](https://clawhub.ai/user/byungkyu) <br>

### License/Terms of Use: <br>
MIT-0 <br>


## Use Case: <br>
Developers and operators use this skill to connect agents to Google Sheets through Maton's managed OAuth gateway, then read values, update ranges, append rows, create spreadsheets, and apply formatting. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Spreadsheet data and OAuth-backed access pass through Maton's third-party API gateway. <br>
Mitigation: Install only if you trust Maton to broker access to the Google Sheets data used with the skill. <br>
Risk: The skill can write to or delete Google Sheets resources when authorized. <br>
Mitigation: Use a least-privileged Google account or connection, specify the intended connection when multiple accounts exist, and approve writes or deletes only after checking the spreadsheet ID, range, and exact effect. <br>
Risk: The skill requires a sensitive MATON_API_KEY credential. <br>
Mitigation: Store the API key in the environment, avoid exposing it in commands or logs, and rotate it if it may have been shared. <br>


## Reference(s): <br>
- [ClawHub Google Sheets release](https://clawhub.ai/byungkyu/google-sheets) <br>
- [Google Sheets API reference](https://developers.google.com/workspace/sheets/api/reference/rest) <br>
- [Maton CLI manual](https://cli.maton.ai/manual) <br>
- [Maton community](https://discord.com/invite/dBfFAcefs2) <br>


## Skill Output: <br>
**Output Type(s):** [text, markdown, guidance, shell commands, code, configuration] <br>
**Output Format:** [Markdown with inline bash, JSON, JavaScript, and Python examples] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Requires network access, a valid MATON_API_KEY, and an active Google Sheets OAuth connection.] <br>

## Skill Version(s): <br>
1.0.8 (source: server release metadata) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>

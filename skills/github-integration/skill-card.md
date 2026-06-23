## Description: <br>
Github integration for managing project management and ticketing data, records, and workflows when a user wants to interact with Github data. <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[membranedev](https://clawhub.ai/user/membranedev) <br>

### License/Terms of Use: <br>
MIT-0 <br>


## Use Case: <br>
Developers and agents use this skill to connect to GitHub through Membrane, discover available GitHub actions, and manage repositories, issues, pull requests, releases, and related project workflows. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: The skill can enable broad authenticated GitHub write actions and raw API requests through Membrane. <br>
Mitigation: Use least-privileged GitHub access, review OAuth scopes, and require explicit confirmation before creating, updating, merging, releasing, deleting, or sending non-GET proxy requests. <br>
Risk: The skill requires external authentication and sensitive GitHub access. <br>
Mitigation: Install only when the publisher is trusted and complete authentication through Membrane rather than sharing raw API keys or tokens with the agent. <br>


## Reference(s): <br>
- [GitHub REST API documentation](https://docs.github.com/en/rest) <br>
- [Membrane homepage](https://getmembrane.com) <br>
- [ClawHub skill page](https://clawhub.ai/membranedev/github-integration) <br>


## Skill Output: <br>
**Output Type(s):** [Guidance, Shell commands, Configuration, API calls] <br>
**Output Format:** [Markdown with inline shell commands and JSON examples] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Requires network access, a valid Membrane account, and a GitHub connection authorized by the user.] <br>

## Skill Version(s): <br>
1.0.5 (source: server release evidence) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>

# Proxmox API token for Blackstart

1. Datacenter → Permissions → Users → Add `blackstart@pve` (or `@pam`).
2. Datacenter → Permissions → API Tokens → Add for that user. **Uncheck Privilege Separation** unless you also add ACL on the token itself.
3. Datacenter → Permissions → Add:
   - Path `/` (or a pool)
   - Role with `VM.Audit`, `VM.PowerMgmt`
   - Optional: `Sys.Audit`. Only add `Sys.PowerMgmt` if a playbook will shut the node down.
4. Paste `user@realm!tokenid` and the secret into Inventory → Proxmox host.

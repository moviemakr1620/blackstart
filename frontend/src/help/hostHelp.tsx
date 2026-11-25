/** Short, reusable copy for Inventory host-form help tips. */

export const HELP_SSH_KEY = {
  title: "SSH private key",
  body: (
    <>
      <p>
        Blackstart uses this to log into the host and run commands (stop a container, shut the machine down, and so
        on). If you already set a login password, the key is optional — pick one method or use both.
      </p>
      <ol className="list-decimal pl-4 space-y-1">
        <li>
          On your computer:{" "}
          <code className="mono text-[11px] bg-ink-800 px-1 py-0.5 rounded">ssh-keygen -t ed25519 -f blackstart_key</code>
        </li>
        <li>
          Install the public key:{" "}
          <code className="mono text-[11px] bg-ink-800 px-1 py-0.5 rounded">
            ssh-copy-id -i blackstart_key.pub user@host
          </code>
        </li>
        <li>
          Open the private file (<span className="mono text-[11px]">blackstart_key</span>, not{" "}
          <span className="mono text-[11px]">.pub</span>) and paste the whole thing, including the{" "}
          <span className="mono text-[11px]">BEGIN OPENSSH PRIVATE KEY</span> lines.
        </li>
      </ol>
      <p>The key is stored encrypted in Blackstart. It is not the same as the “SSH key pinned” fingerprint after Test.</p>
    </>
  ),
};

export const HELP_KEY_PASSPHRASE = {
  title: "Key passphrase",
  body: (
    <>
      <p>
        Only fill this in if you set a passphrase when you ran <span className="mono text-[11px]">ssh-keygen</span>{" "}
        (it asked “Enter passphrase”). If you just pressed Enter then, leave this empty.
      </p>
      <p>This is not the SSH login password for the host user.</p>
    </>
  ),
};

export const HELP_TLS = {
  title: "Verify HTTPS certificate",
  body: (
    <>
      <p>
        When this is on, Blackstart checks that the certificate really belongs to this host, so nobody on the network
        can impersonate it and steal the login.
      </p>
      <p>
        Home-lab Proxmox, PBS, Synology, and OMV usually use a self-signed certificate. Leave verification on and paste
        the lab CA <em>or</em> the server certificate itself — either one works. If you connect by IP, Blackstart still
        checks the signature and does not require the name on the certificate to match the IP.
      </p>
      <p className="font-medium text-slate-200">How to get the certificate</p>
      <ul className="list-disc pl-4 space-y-1">
        <li>
          <code className="mono text-[11px] bg-ink-800 px-1 py-0.5 rounded break-all">
            openssl s_client -connect HOST:8006 -showcerts &lt;/dev/null
          </code>{" "}
          then copy the first <span className="mono text-[11px]">-----BEGIN CERTIFICATE-----</span> block. Use port 8006
          for Proxmox, 8007 for PBS, 5001 for Synology, 443 for OMV.
        </li>
        <li>Or in the browser: open the host UI, click the padlock, show the certificate, and export it as PEM.</li>
        <li>
          Proxmox also keeps the lab CA at <span className="mono text-[11px]">/etc/pve/pve-root-ca.pem</span> on the
          node. Synology can export its certificate from Control Panel → Security → Certificate.
        </li>
      </ul>
    </>
  ),
};

export const HELP_PINNED_KEY = {
  title: "Pinned SSH host key",
  body: (
    <>
      <p>
        This is the <em>server’s</em> fingerprint, saved automatically the first time you click Test. It is not your
        private key, and it is not a password.
      </p>
      <p>
        Later connections check that the host still has the same key, so a fake machine cannot intercept the login. If
        you rebuilt the host (new OS), the fingerprint changes and Test will fail. Click{" "}
        <strong>Forget pinned key</strong>, then Test again to save the new one.
      </p>
    </>
  ),
};

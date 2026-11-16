# Put the host on AWS

One small virtual machine (EC2) that runs the Compose stack. About 20 minutes.
Do it the day before; stop the machine afterwards so it costs nothing.

## 1. Start the machine

In the AWS console, pick the region closest to you (for example **Europe (Stockholm)
eu-north-1**), open **EC2** and choose **Launch instance**.

| Field | Value |
| --- | --- |
| Name | `freezer-workshop` |
| Image | Ubuntu Server 24.04 LTS |
| Instance type | `t3.small` (2 GB memory is plenty for 20 teams) |
| Key pair | Create one, download the `.pem` file |
| Network settings | Allow SSH from **My IP**; tick **Allow HTTPS traffic** and **Allow HTTP traffic** from the internet |
| Storage | 20 GB |

Launch it. On the instance page, note the **Public IPv4 address**, for example
`13.48.1.23`.

The address changes when the machine is stopped and started. If you set it up days
ahead, first allocate an **Elastic IP** (EC2 → Elastic IPs → Allocate, then
Associate with the instance) and use that address.

## 2. Install Docker on it

```bash
chmod 400 freezer-workshop.pem
```

```bash
ssh -i freezer-workshop.pem ubuntu@13.48.1.23
```

On the machine:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

```bash
sudo usermod -aG docker ubuntu && exit
```

Log in again with the same `ssh` command.

## 3. Get the code onto it

The repository is private, so copy it from your computer instead of cloning. On your
computer, in the folder that contains `freezer-workshop-host`:

```bash
rsync -az --exclude .venv --exclude data --exclude .git -e "ssh -i freezer-workshop.pem" freezer-workshop-host ubuntu@13.48.1.23:
```

## 4. Configure and start

On the machine:

```bash
cd freezer-workshop-host && cp .env.example .env && nano .env
```

Set `SITE_PASSWORD`, `ADMIN_PASSWORD`, and the address. For HTTPS without owning a
domain, write the public IP with dashes followed by `.sslip.io`:

```
SITE_ADDRESS=13-48-1-23.sslip.io
```

(`sslip.io` is a public service that makes that name point at that IP, which lets the
stack fetch a real certificate. With a domain of your own, point a DNS A record at
the IP and write that name instead.)

```bash
docker compose up -d --build
```

Open `https://13-48-1-23.sslip.io`. The first visit can take half a minute while the
certificate is fetched. Log in with the organiser password and open **Map**.

## 5. During and after the workshop

| To | Run on the machine, in `freezer-workshop-host` |
| --- | --- |
| See what happens | `docker compose logs -f --tail=100` |
| Restart (stores are kept) | `docker compose restart host` |
| Start from an empty map | `docker compose down && docker volume rm freezer-workshop-host_host-data && docker compose up -d` |
| Update after changing code | `rsync` as in step 3, then `docker compose up -d --build` |

Afterwards: EC2 → select the instance → **Instance state → Terminate** (and release
the Elastic IP if you made one).

## Things that go wrong

- **The page does not load.** The security group must allow ports 80 and 443 from
  anywhere (EC2 → instance → Security → the security group → Inbound rules).
- **Certificate error.** `SITE_ADDRESS` must match the address typed in the browser
  exactly, and port 80 must be open (the certificate is checked over it).
- **Company network blocks `sslip.io`.** Ask IT for a DNS name, or run with
  `SITE_ADDRESS=:80` and use `http://<ip>`; the teams' tokens then travel unencrypted,
  which is acceptable for a workshop with throwaway data but not ideal.

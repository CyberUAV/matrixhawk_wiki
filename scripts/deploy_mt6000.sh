#!/bin/zsh
# Deploy the built site to the MT6000 router's USB drive (LAN preview host).
#
# The router serves it via a dedicated uhttpd instance:
#   uci show uhttpd.matrixhawk   # home=<USB>/matrixhawk_site, port 8642
# LAN URL: http://192.168.8.1:8642/zh/
#
# exFAT notes: the stick keeps the user's other files — sync is scoped to
# the matrixhawk_site/ subdir only (--delete never touches anything else).
# exFAT has no owner/perms, hence the --no-* flags.
set -e

SITE="${1:-$HOME/SynologyDrive/GitHub/matrixhawk_site}"
DEST="root@192.168.8.1:/tmp/mountd/disk1_part1/matrixhawk_site/"

# Secretive (Secure Enclave) key, dropbear on port 68 — see memory
# mt6000-4g-sqm-cake.md; -i takes the .pub with IdentityAgent.
SSH_CMD="ssh -p 68 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
 -o BatchMode=yes \
 -o IdentityAgent=$HOME/Library/Containers/com.maxgoedjen.Secretive.SecretAgent/Data/socket.ssh \
 -o IdentitiesOnly=yes -i $HOME/.ssh/mt6000_secretive.pub"

exec rsync -rlt --no-perms --no-owner --no-group --delete \
  --exclude='.claude' --info=stats1,progress2 \
  -e "$SSH_CMD" "$SITE/" "$DEST"

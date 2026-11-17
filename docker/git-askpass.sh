#!/bin/sh
# Supplies the GitHub token to git over HTTPS (GIT_ASKPASS). Token comes from $GIT_TOKEN.
case "$1" in
  Username*) echo "x-access-token" ;;
  *) echo "$GIT_TOKEN" ;;
esac

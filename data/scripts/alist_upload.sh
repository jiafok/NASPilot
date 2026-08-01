#!/bin/sh

export ALIST_LOG_DIR=/logs
cd /logs
printf "2\n" | script -q -c "python3 /scripts/alist_upload.py" /dev/null
#echo -e "2\n" | python3 /scripts/alist_upload.py

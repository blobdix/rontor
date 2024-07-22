#!/usr/bin/env bash

cd /tmp

# リトライ関数の定義
retry_git_clone() {
    local max_attempts=5
    local attempt=1
    local delay=10

    while [ $attempt -le $max_attempts ]; do
        echo "Attempt $attempt to clone repository..."
        if git clone https://github.com/blobdix/rontor.git; then
            echo "Repository cloned successfully"
            return 0
        else
            echo "Clone failed. Retrying in $delay seconds..."
            sleep $delay
            ((attempt++))
        fi
    done

    echo "Failed to clone repository after $max_attempts attempts"
    return 1
}

# リトライロジックの実行
if retry_git_clone; then
    cd rontor
    python3 boot.py
else
    echo "Failed to clone repository. Exiting."
    exit 1
fi
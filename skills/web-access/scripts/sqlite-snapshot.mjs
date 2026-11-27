import fs from 'node:fs';

// WAL contains committed data that may not be checkpointed into the main DB.
// SHM is a transient WAL index and should be rebuilt by SQLite at the destination.
const SQLITE_COPY_SUFFIXES = ['', '-wal'];
const SQLITE_CLEANUP_SUFFIXES = ['', '-wal', '-shm'];

function restrictSnapshotPermissions(file) {
  try {
    // Browser history is sensitive. Keep the temporary snapshot owner-only on
    // POSIX systems; Windows may ignore or reject POSIX mode bits.
    fs.chmodSync(file, 0o600);
  } catch (error) {
    if (process.platform !== 'win32') throw error;
  }
}

export function copySqliteSnapshot(source, destination) {
  const copied = [];
  try {
    for (const suffix of SQLITE_COPY_SUFFIXES) {
      try {
        const target = `${destination}${suffix}`;
        fs.copyFileSync(`${source}${suffix}`, target);
        restrictSnapshotPermissions(target);
        copied.push(target);
      } catch (error) {
        if (suffix && error?.code === 'ENOENT') continue;
        throw error;
      }
    }
    return copied;
  } catch (error) {
    removeSqliteSnapshot(destination);
    throw error;
  }
}

export function removeSqliteSnapshot(destination) {
  for (const suffix of SQLITE_CLEANUP_SUFFIXES) {
    try { fs.unlinkSync(`${destination}${suffix}`); } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }
}

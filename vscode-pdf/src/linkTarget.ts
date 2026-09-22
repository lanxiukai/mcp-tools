import { posix } from 'path';

export interface DocumentUri {
  scheme: string;
  authority: string;
  path: string;
  query?: string;
  fragment?: string;
}

export type LinkTarget =
  | { kind: 'web'; url: string }
  | { kind: 'local'; uri: DocumentUri };

/** Resolve paths in the PDF's filesystem, independent of the UI host OS. */
export function resolveLink(raw: string, document: DocumentUri): LinkTarget {
  if (typeof raw !== 'string' || !raw.trim() || /[\u0000-\u001f]/.test(raw)) {
    throw new Error('Invalid document link.');
  }
  const value = raw.trim();
  if (/^https?:/i.test(value)) {
    const url = new URL(value);
    return { kind: 'web', url: url.href };
  }
  if (!['file', 'vscode-remote'].includes(document.scheme)) {
    throw new Error(`Unsupported document filesystem: ${document.scheme}`);
  }
  const scheme = /^([a-z][a-z\d+.-]*):/i.exec(value)?.[1].toLowerCase();
  if (scheme && scheme !== 'file' && scheme !== document.scheme) {
    throw new Error(`Unsupported link scheme: ${scheme}`);
  }
  // Encode the source path before using URL resolution; '%' and '#' may be
  // literal filename characters. POSIX URI paths also work on a Windows host.
  const base = new URL('file:///');
  base.pathname = document.path.split('/').map(encodeURIComponent).join('/');
  const url = new URL(value, base);
  if (url.username || url.password) {
    throw new Error('Local document links cannot contain credentials.');
  }
  if (url.protocol === 'vscode-remote:') {
    if (document.scheme !== 'vscode-remote' || url.host !== document.authority) {
      throw new Error('The link points to a different remote connection.');
    }
  } else if (url.host && url.host !== 'localhost' && url.host !== document.authority) {
    throw new Error('The link points to a different filesystem.');
  }
  return {
    kind: 'local',
    uri: {
      scheme: document.scheme,
      authority: document.authority,
      path: posix.normalize(decodeURIComponent(url.pathname)),
      query: url.search.slice(1),
      fragment: decodeURIComponent(url.hash.slice(1)),
    },
  };
}

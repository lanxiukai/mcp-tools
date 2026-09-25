import * as vscode from 'vscode';
import { posix } from 'path';
import { resolveLink } from './linkTarget';

export const PDF_VIEW_TYPE = 'pdfLocalLinks.preview';

async function isFile(uri: vscode.Uri): Promise<boolean> {
  try {
    return ((await vscode.workspace.fs.stat(uri)).type & vscode.FileType.File) !== 0;
  } catch (error) {
    if (error instanceof vscode.FileSystemError && error.code === 'FileNotFound') return false;
    throw error;
  }
}

/** Compatibility for PDFs generated before the converter recorded selections. */
async function legacyPdf(uri: vscode.Uri): Promise<vscode.Uri | undefined> {
  const directory = uri.with({ path: posix.dirname(uri.path), query: '', fragment: '' });
  const stem = posix.basename(uri.path, posix.extname(uri.path));
  const entries = await vscode.workspace.fs.readDirectory(directory);
  const candidates = entries.filter(([name, type]) =>
    (type & vscode.FileType.File) !== 0 && /\.pdf$/i.test(name) &&
    (name.slice(0, -4) === stem || ['-', '_', '.', ' ', '(', '（'].some(s => name.startsWith(stem + s)))
  );
  const competing = entries.some(([name]) =>
    name !== posix.basename(uri.path) && /\.(md|markdown|html|htm)$/i.test(name) &&
    posix.basename(name, posix.extname(name)) === stem
  );
  if (candidates.length === 1 && candidates[0][0].slice(0, -4) === stem && !competing) {
    return vscode.Uri.joinPath(directory, candidates[0][0]).with({ fragment: uri.fragment });
  }
  if (candidates.length) {
    const choices = [
      { label: 'Open source file', uri },
      ...candidates.map(([name]) => ({ label: name, uri: vscode.Uri.joinPath(directory, name).with({ fragment: uri.fragment }) })),
    ];
    const selected = await vscode.window.showQuickPick(choices, {
      title: `Choose a document for ${posix.basename(uri.path)}`,
      placeHolder: 'Several PDF/source interpretations exist; no edition was selected automatically.',
    });
    return selected?.uri;
  }
  return uri;
}

export async function openDocumentLink(
  raw: string,
  document: vscode.Uri,
  resolvedByConverter: boolean,
  navigatePdf: (uri: vscode.Uri, fragment: string) => Promise<void>,
): Promise<void> {
  const target = resolveLink(raw, document);
  if (target.kind === 'web') {
    const config = vscode.workspace.getConfiguration('pdf-local-links', document);
    if (config.get<string>('webLinks', 'vscode') === 'external') {
      await vscode.env.openExternal(vscode.Uri.parse(target.url));
    } else {
      await vscode.commands.executeCommand('simpleBrowser.api.open', vscode.Uri.parse(target.url), { viewColumn: vscode.ViewColumn.Active, preserveFocus: false });
    }
    return;
  }
  let uri = vscode.Uri.from(target.uri);
  const fragment = uri.fragment;
  const file = uri.with({ query: '', fragment: '' });
  if (!(await isFile(file))) {
    throw new Error(`Linked file does not exist: ${uri.path}`);
  }
  if (!resolvedByConverter && /\.(md|markdown|html|htm)$/i.test(uri.path)) {
    const selected = await legacyPdf(uri);
    if (!selected) return;
    uri = selected;
  }
  if (/\.pdf$/i.test(uri.path)) {
    await navigatePdf(uri.with({ query: '', fragment: '' }), fragment);
  } else {
    // Let VS Code's registered editor handle scripts, Markdown, HTML, images,
    // and other local formats. This never executes the linked file.
    await vscode.commands.executeCommand('vscode.open', uri.with({ fragment: decodeURIComponent(uri.fragment) }), { preview: false });
  }
}

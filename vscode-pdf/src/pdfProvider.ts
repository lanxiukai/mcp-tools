import * as vscode from 'vscode';
import { PdfPreview } from './pdfPreview';
import { PDF_VIEW_TYPE } from './openLink';

export class PdfCustomProvider implements vscode.CustomReadonlyEditorProvider {
  public static readonly viewType = PDF_VIEW_TYPE;

  private readonly _previews = new Set<PdfPreview>();
  private _activePreview: PdfPreview | undefined;
  private readonly pending = new Map<string, string>();

  private async openPdfLink(uri: vscode.Uri, fragment: string): Promise<void> {
    const existing = [...this._previews].find(preview => preview.resourceUri.toString() === uri.toString());
    if (existing) {
      existing.reveal();
      existing.navigate(fragment);
      return;
    }
    this.pending.set(uri.toString(), fragment);
    try {
      await vscode.commands.executeCommand('vscode.openWith', uri, PDF_VIEW_TYPE, { preview: false });
    } finally {
      this.pending.delete(uri.toString());
    }
  }

  constructor(private readonly extensionRoot: vscode.Uri) {}

  public openCustomDocument(uri: vscode.Uri): vscode.CustomDocument {
    return { uri, dispose: (): void => {} };
  }

  public async resolveCustomEditor(
    document: vscode.CustomDocument,
    webviewEditor: vscode.WebviewPanel
  ): Promise<void> {
    const preview = new PdfPreview(
      this.extensionRoot,
      document.uri,
      webviewEditor,
      (uri, fragment) => this.openPdfLink(uri, fragment),
    );
    preview.navigate(this.pending.get(document.uri.toString()) || document.uri.fragment);
    this._previews.add(preview);
    this.setActivePreview(preview);

    webviewEditor.onDidDispose(() => {
      preview.dispose();
      this._previews.delete(preview);
    });

    webviewEditor.onDidChangeViewState(() => {
      if (webviewEditor.active) {
        this.setActivePreview(preview);
      } else if (this._activePreview === preview && !webviewEditor.active) {
        this.setActivePreview(undefined);
      }
    });
  }

  public get activePreview(): PdfPreview {
    return this._activePreview;
  }

  private setActivePreview(value: PdfPreview | undefined): void {
    this._activePreview = value;
  }
}

# Refreshes the table of contents in Microsoft Word, saves the DOCX, and exports
# the PDF. Word works on a uniquely named temporary copy so OneDrive sync or a
# stale lock file cannot block it with a hidden prompt.
#
# Two Word sessions are used: on this workstation Word's COM session disconnects
# (RPC_E_DISCONNECTED) immediately after ExportAsFixedFormat, even though the PDF
# is complete, so the export must be the last call of its own session.
$ErrorActionPreference = 'Stop'
# Word automation is reliable here only from a background job process; a direct
# `powershell -File` session deadlocks inside Documents.Open/TOC updates.
$job = Start-Job -ArgumentList $PSScriptRoot -ScriptBlock {
param($PSScriptRoot)
$ErrorActionPreference = 'Stop'
$docx = Join-Path $PSScriptRoot 'CrossTalk_Project_Report.docx'
$pdf = Join-Path $PSScriptRoot 'CrossTalk_Project_Report.pdf'
$work = Join-Path ([IO.Path]::GetTempPath()) ("crosstalk-report-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $work | Out-Null
Copy-Item $docx (Join-Path $work 'in.docx')

function Open-Word {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word
}

# Session 1: refresh the table of contents and save the DOCX.
$word = Open-Word
$doc = $word.Documents.Open((Join-Path $work 'in.docx'), $false, $false)
foreach ($toc in $doc.TablesOfContents) { $toc.Update() }
$doc.Repaginate()
foreach ($toc in $doc.TablesOfContents) { $toc.UpdatePageNumbers() }
$doc.SaveAs2((Join-Path $work 'out.docx'), 16)  # 16 = wdFormatXMLDocument
$doc.Close($false)
$word.Quit()

# Session 2: export the refreshed document to PDF.
$word = Open-Word
$doc = $word.Documents.Open((Join-Path $work 'out.docx'), $false, $true)
$doc.ExportAsFixedFormat((Join-Path $work 'out.pdf'), 17)  # 17 = wdExportFormatPDF
try { $doc.Close($false); $word.Quit() } catch { }
Get-Process WINWORD -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -eq '' -and $_.StartTime -gt (Get-Date).AddMinutes(-5) } |
    Stop-Process -Force

Copy-Item -Force (Join-Path $work 'out.docx') $docx
Copy-Item -Force (Join-Path $work 'out.pdf') $pdf
$pdf
}
if (-not (Wait-Job $job -Timeout 150)) {
    Stop-Job $job
    Get-Process WINWORD -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -eq '' -and $_.StartTime -gt (Get-Date).AddMinutes(-5) } |
        Stop-Process -Force
    throw 'Word export timed out'
}
Receive-Job $job

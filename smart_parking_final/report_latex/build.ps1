$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    for ($pass = 1; $pass -le 3; $pass++) {
        & xelatex -interaction=nonstopmode -halt-on-error -file-line-error main.tex
        if ($LASTEXITCODE -ne 0) { throw "XeLaTeX failed on pass $pass. See main.log." }
    }
    Write-Output "PDF ready: $PSScriptRoot/main.pdf"
}
finally {
    Pop-Location
}

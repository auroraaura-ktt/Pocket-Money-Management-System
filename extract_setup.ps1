$content = Get-Content -Path 'templates/budget/dashboard.html' -Raw
$startMarker = "{% if tab == 'setup' %}"
$endMarker = "{% else %}"
$start = $content.IndexOf($startMarker)
$end = $content.IndexOf($endMarker, $start)
Write-Output $content.Substring($start, $end - $start)

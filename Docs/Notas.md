## JWT

jwt - streamlit 

eyJhbGciOiJFUzI1NiIsImtpZCI6ImQ5ZTBkN2IxLWQwMmQtNDcwMC1iMjVkLWY3YzE1YmVhMWVlNiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwczovL3Fqem91aGRyaG94bXRpbmlkc3F2LnN1cGFiYXNlLmNvL2F1dGgvdjEiLCJzdWIiOiJjNDc2NTIxMC0zOTQ3LTQ3ZmQtODI1Yi1iYmE0OWFmNWFjNzYiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoxNzc5NTM4NjQ2LCJpYXQiOjE3Nzk1MzUwNDYsImVtYWlsIjoiaGVucmlxdWUudGFtYWtpQGdtYWlsLmNvbSIsInBob25lIjoiIiwiYXBwX21ldGFkYXRhIjp7InByb3ZpZGVyIjoiZW1haWwiLCJwcm92aWRlcnMiOlsiZW1haWwiXX0sInVzZXJfbWV0YWRhdGEiOnsiZW1haWwiOiJoZW5yaXF1ZS50YW1ha2lAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsInBob25lX3ZlcmlmaWVkIjpmYWxzZSwicHJlZmVycmVkX2xhbmd1YWdlIjoiZW4iLCJzdWIiOiJjNDc2NTIxMC0zOTQ3LTQ3ZmQtODI1Yi1iYmE0OWFmNWFjNzYifSwicm9sZSI6ImF1dGhlbnRpY2F0ZWQiLCJhYWwiOiJhYWwxIiwiYW1yIjpbeyJtZXRob2QiOiJwYXNzd29yZCIsInRpbWVzdGFtcCI6MTc3OTUzNTA0Nn1dLCJzZXNzaW9uX2lkIjoiMTBlOTdmNDMtZmNjZS00ODllLWEwMjEtMzQ5ZDliNGVhMjMwIiwiaXNfYW5vbnltb3VzIjpmYWxzZX0.PcPA7rH3U34QR8uU81cxZvE5kIYvhnUB51ix_zE-i4yw7Ga-yOomOQhlmohYaFw8ZT52v_JIMjgIvINU8HslQA

$JWT = "eyJhbGciOiJFUzI1NiIsImtpZCI6ImQ5ZTBkN2IxLWQwMmQtNDcwMC1iMjVkLWY3YzE1YmVhMWVlNiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwczovL3Fqem91aGRyaG94bXRpbmlkc3F2LnN1cGFiYXNlLmNvL2F1dGgvdjEiLCJzdWIiOiJjNDc2NTIxMC0zOTQ3LTQ3ZmQtODI1Yi1iYmE0OWFmNWFjNzYiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoxNzc5NTM4NjQ2LCJpYXQiOjE3Nzk1MzUwNDYsImVtYWlsIjoiaGVucmlxdWUudGFtYWtpQGdtYWlsLmNvbSIsInBob25lIjoiIiwiYXBwX21ldGFkYXRhIjp7InByb3ZpZGVyIjoiZW1haWwiLCJwcm92aWRlcnMiOlsiZW1haWwiXX0sInVzZXJfbWV0YWRhdGEiOnsiZW1haWwiOiJoZW5yaXF1ZS50YW1ha2lAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsInBob25lX3ZlcmlmaWVkIjpmYWxzZSwicHJlZmVycmVkX2xhbmd1YWdlIjoiZW4iLCJzdWIiOiJjNDc2NTIxMC0zOTQ3LTQ3ZmQtODI1Yi1iYmE0OWFmNWFjNzYifSwicm9sZSI6ImF1dGhlbnRpY2F0ZWQiLCJhYWwiOiJhYWwxIiwiYW1yIjpbeyJtZXRob2QiOiJwYXNzd29yZCIsInRpbWVzdGFtcCI6MTc3OTUzNTA0Nn1dLCJzZXNzaW9uX2lkIjoiMTBlOTdmNDMtZmNjZS00ODllLWEwMjEtMzQ5ZDliNGVhMjMwIiwiaXNfYW5vbnltb3VzIjpmYWxzZX0.PcPA7rH3U34QR8uU81cxZvE5kIYvhnUB51ix_zE-i4yw7Ga-yOomOQhlmohYaFw8ZT52v_JIMjgIvINU8HslQA"


$envFile = Get-Content "e:\BD\260502 BI TopStep\Env\Topstep_bi.env"
$ANON = ($envFile | Select-String "^SUPABASE_ANON_KEY=sb_publishable_8iAxNO7K3EUmKobtGpegzA_PdLGfAoW").ToString().Split("=",2)[1].Trim('"').Trim("'")

$JWT.Length
$ANON.Length


$ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFqem91aGRyaG94bXRpbmlkc3F2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgwMTk2MzIsImV4cCI6MjA5MzU5NTYzMn0.jIXFHCsPPad4c7iMNNY0pYpE9_aJmvf91Ik3P3aAg-0"



eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFqem91aGRyaG94bXRpbmlkc3F2Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODAxOTYzMiwiZXhwIjoyMDkzNTk1NjMyfQ.8TDkmXAxnMVCE12NrvAAP2d4EaHAU4ILZMF8FGGMrpQ



$EMAIL = "henrique.tamaki@gmail.com"
$PASS  = "xcitya21"

$body = @{ email=$EMAIL; password=$PASS } | ConvertTo-Json
$resp = Invoke-RestMethod -Uri "https://qjzouhdrhoxmtinidsqv.supabase.co/auth/v1/token?grant_type=password" `
  -Method POST `
  -Headers @{ "apikey"=$ANON; "content-type"="application/json" } `
  -Body $body

$JWT = $resp.access_token
$UID = $resp.user.id
Write-Host "JWT inicio: $($JWT.Substring(0,30))..."
Write-Host "User ID:   $UID"





# 1. ANON key — pega do .streamlit/secrets.toml ou Env/Topstep_bi.env
$ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFqem91aGRyaG94bXRpbmlkc3F2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgwMTk2MzIsImV4cCI6MjA5MzU5NTYzMn0.jIXFHCsPPad4c7iMNNY0pYpE9_aJmvf91Ik3P3aAg-0"

# 2. Login → JWT
$body = @{
  email = "henrique.tamaki@gmail.com"
  password = "xcitya21"
} | ConvertTo-Json

$resp = Invoke-RestMethod -Uri "https://qjzouhdrhoxmtinidsqv.supabase.co/auth/v1/token?grant_type=password" `
  -Method POST `
  -Headers @{ "apikey"=$ANON; "content-type"="application/json" } `
  -Body $body

$JWT = $resp.access_token
Write-Host "JWT pego: $($JWT.Substring(0,30))..."

# 3. Payload CORRETO (snapshot_at UTC + nomes certos)
$payload = @{
  account_id         = "TEST-ACC-001"
  snapshot_at        = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
  position_contract  = "MES"
  position_side      = "Long"
  position_size      = 1
  position_avg_price = 5800.50
  unrealized_pnl     = 2.50
  realized_pnl       = 0
  day_pnl            = 0
  drawdown           = 0
} | ConvertTo-Json

# 4. POST
Invoke-RestMethod -Uri "https://qjzouhdrhoxmtinidsqv.supabase.co/functions/v1/live-ingest" `
  -Method POST `
  -Headers @{ "authorization"="Bearer $JWT"; "apikey"=$ANON; "content-type"="application/json" } `
  -Body $payload




insert into public.alerts (user_id, alert_type, severity, title, body)
values (
  '<c4765210-3947-47fd-825b-bba49af5ac76>',
  'test',
  'critical',
  'Teste Realtime BI TopStep',
  'Se esta notificação aparecer, o pipeline ponta-a-ponta está OK.'
);

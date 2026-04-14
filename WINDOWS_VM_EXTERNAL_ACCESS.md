# Expose Ubuntu VM App Through Windows Host `IP:18080`

이 문서는 Windows에서 띄운 Ubuntu VM 안에서 `uvicorn`으로 실행 중인 앱을,
Windows 호스트의 `IP:18080`으로 외부에서도 접속 가능하게 만드는 방법을 정리합니다.

예시 목표:

- Ubuntu VM 앱: `http://<ubuntu-vm-ip>:8000`
- 외부 노출 주소: `http://<windows-host-ip>:18080`

## 구조

```text
외부 브라우저
  -> Windows 호스트 IP:18080
  -> Windows portproxy / 방화벽 허용
  -> Ubuntu VM IP:8000
  -> uvicorn app.main:app
```

## 1. Ubuntu VM에서 앱을 외부 바인딩으로 실행

Ubuntu VM 안에서 앱을 `127.0.0.1`이 아니라 `0.0.0.0`으로 띄워야 합니다.

```bash
cd ~/project/video_caption
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

확인:

```bash
ss -ltnp | grep 8000
```

정상이라면 `0.0.0.0:8000` 또는 VM IP 기준으로 리슨 중이어야 합니다.

## 2. Ubuntu VM IP 확인

Ubuntu VM 안에서:

```bash
hostname -I
```

예:

```text
192.168.56.101
```

아래 설명에서는 이 값을 `<ubuntu-vm-ip>`로 사용합니다.

## 3. Windows에서 18080 포트를 Ubuntu VM 8000으로 프록시

Windows PowerShell을 관리자 권한으로 열고 실행:

```powershell
netsh interface portproxy add v4tov4 `
  listenaddress=0.0.0.0 `
  listenport=18080 `
  connectaddress=<ubuntu-vm-ip> `
  connectport=8000
```

예:

```powershell
netsh interface portproxy add v4tov4 `
  listenaddress=0.0.0.0 `
  listenport=18080 `
  connectaddress=192.168.56.101 `
  connectport=8000
```

설정 확인:

```powershell
netsh interface portproxy show all
```

## 4. Windows 방화벽에서 18080 허용

Windows PowerShell을 관리자 권한으로 열고 실행:

```powershell
New-NetFirewallRule `
  -DisplayName "Video Caption 18080" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 18080
```

필요하면 기존 규칙 확인:

```powershell
Get-NetFirewallRule -DisplayName "Video Caption 18080"
```

## 5. Windows 호스트 IP 확인

Windows에서:

```powershell
ipconfig
```

실제 외부에서 접속할 IPv4 주소를 확인합니다. 보통 `Ethernet` 또는 `Wi-Fi` 어댑터의 IPv4 주소입니다.

예:

```text
192.168.0.23
```

이제 같은 네트워크의 다른 PC나 휴대폰에서 아래 주소로 접속합니다.

```text
http://192.168.0.23:18080
```

## 6. Ubuntu 방화벽 확인

Ubuntu에서 `ufw`를 쓰고 있으면 8000 포트 허용이 필요할 수 있습니다.

```bash
sudo ufw allow 8000/tcp
sudo ufw status
```

## 7. 인터넷 외부에서 접속하려면

같은 LAN이 아니라 인터넷 외부에서 접속하려면, 공유기나 클라우드 네트워크 장비에서 추가 포트 포워딩이 필요합니다.

예:

- 외부 `TCP 18080`
- 내부 목적지 `Windows 호스트 IP:18080`

즉, 흐름은 다음과 같습니다.

```text
인터넷
  -> 공유기 공인 IP:18080
  -> Windows 호스트 IP:18080
  -> Ubuntu VM IP:8000
```

주의:

- 공인 인터넷에 그대로 여는 것은 위험할 수 있습니다.
- 가능하면 사내망/VPN 뒤에서만 열거나, reverse proxy + 인증을 붙이세요.

## 8. 자주 막히는 원인

### 앱이 `127.0.0.1`에만 바인딩된 경우

이 경우 Windows가 Ubuntu VM의 8000 포트에 붙지 못합니다.

해결:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Windows 방화벽이 18080을 막는 경우

외부 기기에서는 `연결 거부` 또는 `응답 없음`으로 보일 수 있습니다.

해결:

- `New-NetFirewallRule ... -LocalPort 18080`

### VM 네트워크가 Host-only / 내부망만 되는 경우

이 경우 Windows는 붙더라도 외부 장치가 Windows를 통해 우회 접속하지 못할 수 있습니다.

권장:

- VM 네트워크를 `Bridged` 또는 외부 통신 가능한 모드로 변경
- 아니면 현재 방식대로 Windows `portproxy` 사용

### Windows가 Ubuntu VM IP에 못 붙는 경우

Windows에서 먼저 확인:

```powershell
curl http://<ubuntu-vm-ip>:8000/api/health
```

이게 안 되면 `portproxy`를 잡아도 외부 접속은 안 됩니다.

## 9. 점검 순서

1. Ubuntu VM에서 앱 실행

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

2. Ubuntu 내부에서 확인

```bash
curl http://127.0.0.1:8000/api/health
```

3. Windows에서 Ubuntu VM으로 직접 확인

```powershell
curl http://<ubuntu-vm-ip>:8000/api/health
```

4. Windows `portproxy` 등록

5. Windows 자기 자신으로 확인

```powershell
curl http://127.0.0.1:18080/api/health
```

6. 다른 장치에서 확인

```text
http://<windows-host-ip>:18080
```

## 10. 설정 삭제 방법

`portproxy` 삭제:

```powershell
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=18080
```

방화벽 규칙 삭제:

```powershell
Remove-NetFirewallRule -DisplayName "Video Caption 18080"
```

## 11. 추천 운영 방식

개발/테스트용이면 아래 조합이 가장 단순합니다.

1. Ubuntu VM:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

2. Windows 관리자 PowerShell:

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=18080 connectaddress=<ubuntu-vm-ip> connectport=8000
New-NetFirewallRule -DisplayName "Video Caption 18080" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 18080
```

3. 외부 접속:

```text
http://<windows-host-ip>:18080
```

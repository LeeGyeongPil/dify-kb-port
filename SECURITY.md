# 보안 정책 / Security policy

보안 취약점을 발견하셨다면 **공개 이슈 대신** 이 레포의 **Security → Report a vulnerability**(비공개 취약점 신고)로 알려 주세요. 확인 후 답변드리고, 고친 버전을 릴리스하면서 함께 안내합니다.

If you find a security issue, please **do not open a public issue**. Use **Security → Report a vulnerability** in this repository to report it privately. We will reply after checking and announce the fix with a release.

이 도구는 Dify Knowledge API 키를 다룹니다. 키는 환경변수로만 읽고 묶음·체크포인트·오류 메시지에 쓰지 않으며, 연습용 목 서버는 내 컴퓨터(127.0.0.1)에서만 접속됩니다. / This CLI tool handles Dify Knowledge API keys. Keys are read only from environment variables and are never written to bundles, checkpoints or error messages; the practice mock server listens on your own machine (127.0.0.1) only.

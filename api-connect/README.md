# API Connect

Uma central local de integrações REST. Explore serviços públicos predefinidos, envie chamadas validadas, acompanhe limites de uso e consulte respostas e erros no histórico SQLite.

## Executar no Windows

Requer Python 3.10+ e conexão com a internet para chamar os provedores.

```powershell
Set-Location .\api-connect
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Abra `http://127.0.0.1:8000`. A documentação interativa fica em `http://127.0.0.1:8000/docs`; a verificação de saúde está em `http://127.0.0.1:8000/api/health`.

O SQLite é criado automaticamente em `backend/integration_hub.sqlite3`. Para escolher outro caminho, defina `INTEGRATION_DB` antes de iniciar o servidor. Configure `RATE_LIMIT_SECRET` para um segredo aleatório se quiser um hash de cliente estável e exclusivo para seu ambiente:

```powershell
$env:INTEGRATION_DB = "backend\integration_hub.sqlite3"
$env:RATE_LIMIT_SECRET = "use-um-segredo-aleatorio-aqui"
```

## Integrações incluídas

- **JSONPlaceholder** — listar posts e usuários, consultar um post e criar um post demonstrativo. O serviço de demonstração não persiste posts criados.
- **Open-Meteo** — buscar cidades e consultar clima/previsão com coordenadas. Não requer chave de API.
- **GitHub** — consultar informações de um repositório público, sem autenticação.

As URLs e operações dos provedores são definidas no servidor. Parâmetros passam por validação específica e não é possível enviar uma URL arbitrária pela interface ou pela API.

## Recursos

- Interface adaptável com visão geral, operações, monitoramento e histórico detalhado.
- Formulários gerados de acordo com cada operação e visualização/cópia da resposta JSON.
- Ativar ou pausar integrações; configurar um limite de **1 a 120 chamadas por minuto** por provedor e endereço de cliente.
- Respostas `X-RateLimit-Limit`, `X-RateLimit-Remaining` e `X-RateLimit-Reset` nas chamadas.
- Registro persistente de método, operação, parâmetros, status, duração e resposta; busca, filtros, paginação e limpeza do histórico.
- Timeout de 8 segundos, limite de 1 MB por resposta do provedor, erros HTTP/timeout/conexão identificados e histórico limitado aos 10.000 registros mais recentes.
- Indicadores e gráficos de volume e sucesso das últimas 24 horas.

Os limites são compartilhados entre operações do mesmo provedor e cliente. Respostas completas no histórico são limitadas a 50 KB. Chamadas falhas também são registradas; erros do provedor são apresentados como HTTP 502 e timeouts como HTTP 504. O painel não guarda o endereço IP em texto aberto, usando um HMAC para aplicar limites por cliente.

## API principal

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/health` | Estado da API e do SQLite |
| `GET` | `/api/dashboard` | Indicadores e atividade recente |
| `GET` | `/api/integrations` | Provedores, operações e configurações |
| `PATCH` | `/api/integrations/{provider}/settings` | Ativar/pausar e ajustar limite |
| `POST` | `/api/integrations/{provider}/{operation}` | Validar parâmetros e executar uma chamada permitida |
| `GET` | `/api/logs` | Consultar histórico com filtros e paginação |
| `GET` | `/api/logs/{id}` | Ver detalhes e resposta guardada |
| `DELETE` | `/api/logs` | Limpar o histórico (não apaga os contadores do limite) |

Exemplo de chamada local:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/integrations/jsonplaceholder/posts.list" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"parameters":{"limit":5}}'
```

## Testes

```powershell
python -m unittest discover -s tests -v
```

Os testes usam um banco SQLite temporário e simulam os provedores externos.

## Privacidade e publicação

As respostas e os parâmetros enviados são guardados no SQLite local. Não envie dados pessoais, credenciais ou informações confidenciais. Cada provedor tem seus próprios termos e limites de uso. Esta versão é destinada ao desenvolvimento local e não tem autenticação; mantenha o servidor vinculado a `127.0.0.1`. Antes de expor a terceiros, implemente autenticação, autorização, HTTPS, proteção contra abuso e gestão segura de segredos.

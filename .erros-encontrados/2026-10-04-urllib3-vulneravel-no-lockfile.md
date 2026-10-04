# Vulnerabilidades de urllib3 no lockfile

- Data: 2026-10-04.
- Local: `requirements.lock`, pacote `urllib3==2.7.0`.
- Problema: `python -m pip_audit -r requirements.lock --no-deps --disable-pip` identificou PYSEC-2026-4177, PYSEC-2026-4176 e PYSEC-2026-4175. A ferramenta informa correção em 2.8.0.
- Impacto: dependência com vulnerabilidades conhecidas; a auditoria atual não está verde. A explorabilidade no editor não foi determinada nesta operação de Git.
- Sugestão: atualizar a resolução e os hashes do lockfile para uma versão corrigida compatível e repetir auditoria e testes, em tarefa própria.
- Relação com a tarefa: encontrado antes do commit e push solicitados para salvar o trabalho existente. Nenhuma dependência foi alterada. A suíte atual passou com 148 testes; esse resultado não elimina os achados de segurança.

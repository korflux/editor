# Preferências da interface (resolução, volume) se perdem a cada `ui start`

- **Data:** 2026-10-09
- **Onde:** `src/editor/ui/server.py` (`UiServer.__init__`: `super().__init__((HOST, 0), ...)`, porta livre sorteada a cada instância) e `src/editor/ui/static/store.js` (`readPref` e `writePref`, `localStorage`).
- **Descrição:** o `localStorage` do navegador é isolado por origem, e a origem inclui a porta (`http://127.0.0.1:<porta>`). Como cada `editor ui start` escolhe uma porta nova, a resolução, o volume e o mudo "lembrados por trabalho" (T4) só valem enquanto a mesma instância viver (recarregar a página, trocar de aba). Depois de `ui stop` e novo `ui start`, o navegador abre uma origem sem dados e a interface volta a `full` e volume 1.
- **Impacto conhecido:** só conveniência. Nenhum dado do trabalho nem da aprovação depende disso. O operador que reabre a interface a cada pedido da IA precisa escolher a resolução de novo.
- **Sugestão de correção:** o `ui start` poderia reutilizar a última porta usada para o mesmo trabalho (guardada no registro de `.tmp` ou ao lado do plano) quando ela estiver livre, o que mantém a origem e o `localStorage`. Alternativa: guardar a preferência em cookie sem `HttpOnly` (cookies não são isolados por porta), com prefixo e nome do trabalho. Qualquer das duas muda a T3 (instância e sessão), então fica fora da T4.
- **Relação com a tarefa atual:** T4 (player e resolução lembrada por trabalho). A T4 grava com `try/catch` conforme pedido e funciona dentro da mesma instância; a limitação é do endereço efêmero.

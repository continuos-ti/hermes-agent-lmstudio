# Operação Confiável com LM Studio

Este documento registra os ajustes e as regras operacionais adotadas para usar o
Hermes com o modelo `ornith-1.5-9b-uncensored` servido pelo LM Studio. O foco é
evitar crescimento indefinido de contexto, leituras repetidas, respostas
truncadas e chamadas de ferramenta sem progresso.

## Diagnóstico que motivou os ajustes

Os erros observados não eram uma única falha do provedor. Uma solicitação longa
abria um ciclo: o agente acumulava resultados grandes de terminal e navegador,
tentava reler o mesmo arquivo, enviava um histórico cada vez maior ao modelo e,
por fim, recebia respostas muito curtas ou truncadas. As tentativas automáticas
de continuação repetiam o mesmo planejamento e esgotavam a reserva de contexto.

Os sintomas associados foram:

- `Context length exceeded` ou recusa de injeção de contexto;
- `Response remained truncated after 4 continuation attempts`;
- repetição de leituras de um mesmo intervalo de arquivo;
- mensagem de template do Ornith sem uma consulta de usuário utilizável;
- `database is locked` ao listar sessões durante uma gravação;
- ferramentas de navegador indisponíveis quando nenhum navegador estava
  conectado.

## Invariantes antes de chamar o modelo

Toda chamada ao LM Studio deve passar por uma preparação do histórico:

1. Remover ou substituir mensagens vazias que não sejam a última mensagem da
   conversa. O template do Ornith precisa receber uma consulta de usuário não
   vazia.
2. Garantir que a solicitação do usuário permaneça no payload como uma âncora.
   Isso impede que um histórico composto quase só por ferramentas e respostas
   internas produza a falha `No user query found in messages`.
3. Medir a estimativa de tokens do histórico *antes* de montar a chamada e
   reservar espaço real para a próxima resposta.
4. Não usar a mesma mensagem de histórico duas vezes durante uma tentativa de
   recuperação.

Essas regras devem ser aplicadas tanto a chamadas normais quanto a continuações
após resposta interrompida.

## Orçamento de contexto e poda

O limite configurado no LM Studio não é todo utilizável pelo Hermes. O agente
deve reservar uma faixa para a resposta e para a estrutura de ferramentas. A
política é:

1. Calcular `tokens do histórico + tokens máximos de saída`.
2. Quando o total se aproximar do limite do modelo, podar antes da chamada, e
   não apenas depois de um erro.
3. Preservar sempre: a solicitação atual, o objetivo ativo, decisões já tomadas,
   mudanças realizadas, erros ainda abertos e os últimos resultados essenciais.
4. Descartar primeiro resultados brutos extensos de terminal, páginas obtidas
   pela web, saídas duplicadas e tentativas de ferramenta que não mudaram o
   estado.
5. Substituir um bloco descartado por um resumo curto e factual que mantenha o
   caminho, o resultado, a falha e a próxima ação necessária.

A poda determinística de resultados de ferramenta é preferível a pedir que o
modelo resuma um histórico já cheio. Ela funciona mesmo quando não há espaço
para outra resposta útil.

## Recuperação de respostas longas

Uma resposta truncada não deve disparar quatro continuações idênticas. A ordem
de recuperação é:

1. Detectar se a resposta terminou por limite de geração.
2. Verificar se a continuação planejada é nova; se ela repetir o mesmo prefixo,
   interromper o ciclo.
3. Podar resultados de ferramenta e mensagens internas não essenciais.
4. Reduzir o orçamento de saída para caber no contexto restante e pedir apenas
   a próxima etapa verificável.
5. Se a tarefa ainda for grande, criar um checkpoint com o estado do trabalho e
   dividir a execução em partes independentes. A parte seguinte recebe somente
   o checkpoint, o objetivo e os artefatos necessários.

O limite de continuações continua sendo uma proteção final. Chegar a ele deve
encerrar a tentativa com um checkpoint, não repetir a mesma resposta.

## Divisão de solicitações e execução

Solicitações extensas devem ser convertidas em etapas pequenas, com condição de
conclusão clara. Uma etapa deve ter apenas os arquivos, URLs e ferramentas
necessários para ela.

- Ler arquivos grandes em intervalos, anotando quais intervalos já foram lidos.
- Não reler um intervalo imutável que já foi retornado; usar o conteúdo em
  memória ou o resumo correspondente.
- Extrair referências ou URLs primeiro e buscar somente as que atendem à etapa
  atual.
- Limitar a saída de terminal e registrar apenas os trechos que afetam a decisão
  seguinte.
- Ao concluir uma etapa, gravar um checkpoint conciso antes de iniciar a
  próxima.

Isso permite receber uma instrução grande do usuário sem enviar sua totalidade,
mais todo o histórico bruto, em cada chamada ao modelo.

## Uso correto de ferramentas

Ferramentas devem existir para produzir evidência nova, não para manter a
conversa em movimento.

- `read_file`: solicitar apenas o intervalo necessário; bloquear releituras
  idênticas sem alteração no arquivo.
- Terminal: usar consultas específicas e limites de saída; não listar árvores ou
  logs inteiros repetidamente.
- Navegador e Brave Search: verificar disponibilidade antes da ação. Se não
  houver navegador conectado, informar a limitação e não repetir a chamada.
- Pesquisa web: consultar a API Brave configurada pela interface; recuperar uma
  fonte por vez quando a decisão depender dela; resumir o resultado antes da
  próxima consulta.
- Ferramentas que retornarem erro: registrar causa e escolher uma alternativa;
  não repetir o mesmo comando sem alteração de parâmetros ou estado.

Antes de uma sessão de pesquisa, validar a chave Brave com uma busca simples e
confirmar que a ferramenta retorna resultados. Uma chave cadastrada na
interface, sozinha, não confirma que o caminho de execução está funcional.

## Memória persistente

A memória não deve ser usada como depósito do transcript. Ela serve para manter
preferências estáveis, decisões de arquitetura e fatos que continuam válidos em
sessões futuras.

Para este uso, o provedor Holographic foi escolhido em lugar de `Apenas
embutido`, com memória persistente e perfil do usuário habilitados. A troca não
elimina a necessidade de poda: memória recuperada também consome contexto e
precisa ser limitada por relevância.

Regras de memória:

- gravar fatos curtos e duráveis, não saídas inteiras de ferramentas;
- recuperar poucas memórias diretamente relacionadas ao objetivo atual;
- manter limite próprio para perfil e memória recuperada;
- remover memórias de teste, duplicadas ou que contradigam decisões atuais.

## Banco de sessões

O erro `database is locked` é secundário ao problema de contexto, mas prejudica
a interface durante sessões longas. A camada de estado deve:

1. Ter uma única rotina escritora por perfil de sessão.
2. Abrir leituras da interface como somente leitura, com `busy_timeout` e
   tentativas breves com espera progressiva.
3. Fechar cursores e conexões imediatamente após cada operação.
4. Usar WAL apenas se a versão e o ambiente do SQLite o suportarem. Caso a base
   esteja em modo `DELETE`, serializar escrita e leitura concorrentes em vez de
   forçar uma mudança de journal incompatível.

Não é aceitável silenciar esse erro: a interface deve responder com tentativa
controlada ou estado temporariamente indisponível, sem corromper a sessão.

## Validação antes de uso prolongado

Executar estes cenários depois de qualquer mudança no provider, modelo,
compactor, memória ou ferramentas:

1. Solicitação curta sem ferramentas: confirmar resposta normal e payload com
   consulta de usuário válida.
2. Arquivo grande: confirmar leitura por intervalos, sem repetição de intervalos
   já consumidos.
3. Pesquisa Brave: validar uma consulta e uma fonte, com resultado resumido.
4. Muitas saídas de ferramenta: confirmar poda antes do limite e preservação do
   objetivo atual.
5. Resposta propositalmente longa: confirmar checkpoint, poda e divisão da
   próxima etapa em vez de ciclos de continuação.
6. Duas consultas simultâneas à interface: confirmar que a lista de sessões não
   fica bloqueada pelo SQLite.
7. Nova sessão: confirmar que não recebe transcript bruto da sessão anterior,
   apenas memória relevante explicitamente recuperada.

Os logs devem ser avaliados pelos indicadores: tokens de entrada e saída por
chamada, quantidade de mensagens e ferramentas enviadas, podas realizadas,
continuações, repetições de ferramenta, latência e falhas de SQLite. Uma curva
de contexto continuamente crescente é uma regressão, mesmo quando a conversa
ainda não produziu erro.

## Checklist operacional

- LM Studio está ativo, com o modelo correto carregado e endpoint acessível.
- Hermes aponta somente para o provider LM Studio configurado.
- A chave Brave foi validada por uma consulta real.
- O navegador está conectado quando a tarefa exige navegação.
- Holographic está ativo e não contém memória de testes desnecessária.
- A sessão começa com objetivo e limite de escopo claros.
- Poda preventiva e checkpoint estão ativos antes do limite de contexto.
- Não há loops de continuação, leitura ou ferramenta nos logs recentes.


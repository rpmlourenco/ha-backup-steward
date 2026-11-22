# L4 — Espelho direto para o HA2

L4 envia as origens escolhidas por rsync sobre SSH para a app **Data Steward Mirror**.
É um espelho unidirecional: ficheiros novos, modificados e eliminados na origem
refletem-se no destino. Não há Restic, VPN, armazenamento cloud intermédio ou
montagem de partilhas WAN. Os ficheiros regulares mantêm a estrutura original.

## Preparação manual

1. Instala a app Mirror no HA2 e prepara um disco ext4 dedicado, montado diretamente
   em `/media`. O recetor não monta/desmonta discos nem liga tomadas.
2. Em Alicante, abre **Configurar espelho**. Mantém **Ativar L4** desligado enquanto
   preparas o destino. Escolhe as origens existentes, inicialmente Immich e
   FileBrowser. O código aceita qualquer origem já configurada, sem exceções por nome.
3. Usa **Detetar UUID** ou confirma o UUID do filesystem de cada origem. Guarda.
4. Prepara as chaves públicas e importa o JSON de emparelhamento na app Mirror.
   Cada origem recebe uma chave Ed25519 e uma conta remota separadas.
5. Na app Mirror, guarda o disco/UUID/porta e exporta a identidade do recetor.
   Importa-a no Steward de Alicante: preenche a chave SSH, a porta e o UUID remoto.
   Preenche o endereço DNS/IP, sem `https://` nem caminho.
6. Publica manualmente a porta interna 2222/TCP da app Mirror no HA2 e configura
   router/firewall. Ativa o recetor e reinicia manualmente apenas essa app.
7. Usa **Testar ligação** no emissor: confirma autenticação, identidade, UUID e
   espaço livre do recetor, sem criar ou apagar ficheiros de dados. O painel mostra
   «A testar ligação» e depois «Ligação confirmada» ou «Teste falhou», com data e
   detalhe. Este resultado permanece visível mesmo com L4 desligado e após recarregar.
8. Ativa **L4** e, em modo de produção, escolhe **Sincronizar agora**. O diálogo
   confirma explicitamente que as eliminações também serão espelhadas.

O HA2 está inicialmente em Alicante: usa o IP LAN para acelerar a primeira carga.
Depois de mudar para Lisboa, preserva o disco/chaves e atualiza o endereço/porta.
O domínio fornecido foi `home2.rpmloureco.uk`; a resolução/ortografia não ficou
confirmada. Não foi gravado como destino e nenhuma configuração de rede foi alterada.
O reverse proxy HTTP que dá acesso ao HA2 não encaminha SSH: precisa de TCP próprio.

## Execução automática e manual

- **Ativar L4:** permite ou bloqueia todos os novos envios. Desativar cancela e
  aguarda o processo de transferência; o que já chegou ao destino fica preservado.
- L4 arranca após cada backup local de produção concluído com sucesso, apenas
  depois do fecho seguro do disco, confirmação da tomada desligada e libertação
  da reserva local. Aplica-se aos backups manuais e aos da agenda local.
- Não há agenda L4 separada. As antigas opções de hora/agenda L4 são descartadas
  ao carregar a configuração, sem executar um envio por estar atrasado.
- **Sincronizar agora:** executa apenas o L4, com confirmação, sem ligar o disco
  HAL3 nem executar backups locais. Permite repetir um envio remoto que falhou.
- Falhas/cancelamentos locais, recuperação, restauro, checksum isolado e modo de
  teste não desencadeiam um envio automático. Falhar L4 não repete o backup local.
- L4 partilha a exclusão mútua das operações locais. Aguarda a conclusão de L1–L3,
  restauro ou sessão manual de disco; uma recuperação local pendente bloqueia-o.
- Falhas de rede afetam o resultado L4, não as datas/resultados L1–L3. O espelho
  não mantém HAL3 ligado e não interfere no seu fecho seguro.
- Observação/teste nunca enviam dados para o espelho de produção. O teste explícito
  de ligação é apenas uma sonda de leitura autenticada.

## Proteções e rapidez

Antes do envio, a app valida paths/UUID ext4 das origens, bloqueia árvores vazias
e quedas excessivas relativamente à última cópia L4 concluída. Uma passagem dry-run
conta eliminações e volume a substituir antes de escrever nessa origem. O limite
de eliminações é também imposto no rsync real. O espaço livre é confirmado antes
e depois; os ficheiros eliminados são processados no fim da sincronização.

O rsync compara tamanho/data e usa transferência delta para ficheiros modificados.
SSH e rsync não comprimem fotos/vídeos por defeito. As origens são enviadas em série
para evitar concorrência de leitura/escrita no hardware HA. A velocidade apresentada
é a do progresso rsync, não uma medição independente do débito WAN. Não foi medido
o desempenho nos equipamentos ou ligações reais. Não há promessa de saturar a rede.

Não há escrita `--inplace`: os ficheiros são substituídos individualmente depois
de transferidos, com parciais confinados para permitir aproveitamento posterior.
O conjunto completo não é uma transação: uma interrupção pode deixar uma árvore
parcialmente atualizada. Repete o envio para reconciliar.

As chaves privadas ficam em `/data/steward/l4-ssh`, com permissões restritas e
incluídas no backup privado da app. O browser recebe apenas chaves públicas. A
identidade de host é fixada, sem descoberta/aceitação automática. Uma troca do
disco ou chave exige atualizar explicitamente a configuração.

O recetor usa `rrsync -wo -munge`; links simbólicos são neutralizados e ficheiros
especiais/ACL/proprietários de SO não são o objetivo deste espelho de dados.

## Consistência das aplicações

L4 espelha diretórios. Não pára Immich/FileBrowser nem cria dumps automaticamente.
Prepara previamente dumps consistentes de BD e configuração, dentro de origens
geridas, antes da janela de envio. A BD PostgreSQL do Immich pode estar fora de
`/media/immich`; uma cópia das fotografias, por si só, não recupera os metadados.
O FileBrowser também pode guardar BD/configuração na área privada da sua app.

## Validação

Os testes de unidade usam apenas ficheiros temporários e APIs falsas. As interfaces
têm testes de browser com endpoints simulados, desktop/mobile, toggle L4, chaves
públicas, confirmação e bloqueio em falha de ligação. `tests/ui_l4.py` guarda as
pré-visualizações em `data/l4-previews/`, ignorado no Git.

O repositório Mirror inclui `tests/e2e.py`, com SSH/rsync reais num contentor efémero,
sem dados/dispositivos dos HA; a identidade de montagem é simulada. A instalação,
visibilidade dos UUID no HAOS e o restauro de uma amostra exigem aceitação manual.

## Progresso e registos (0.2.2)

O painel L4 mostra a fase, origem atual, origens concluídas, bytes lógicos processados,
ficheiros transferidos, ritmo rsync, estimativa restante, duração e última atualização.
Durante validação/planeamento, a barra fica indeterminada. O parser aceita linhas
partidas entre leituras e terminações CR/LF; o último contador é publicado no fim.
A percentagem/ETA são as do rsync e podem mudar enquanto a lista cresce. Os bytes
incluem conteúdo reutilizado pelo algoritmo delta; não são uma medição do tráfego WAN.

O histórico L4 guarda até 40 eventos por execução, com data/hora no fuso da app.
Os logs da app recebem início, fases, plano, conclusão por origem, falha/interrupção
e progresso até uma vez por minuto, além do contador final. Não registam nomes de
ficheiros, comandos SSH, chaves ou respostas remotas arbitrárias. O progresso L4
não altera o resumo nem as datas dos backups locais.

O checksum não mudou: a seleção usa tamanho/data; o protocolo rsync valida os
ficheiros reconstruídos durante a transferência. Não há comparação SHA-256 integral
independente de toda a biblioteca remota. A tabela de integridade L3 refere-se ao HAL3.

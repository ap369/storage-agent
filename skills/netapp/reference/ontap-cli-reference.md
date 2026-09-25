# NetApp ONTAP CLI/API quick reference

- `volume create -vserver <svm> -volume <name> -size <size> -state online` — create a volume
- `volume show -vserver <svm>` — list volumes
- `export-policy create -vserver <svm> -policyname <name>` — create an export policy
- `export-policy rule create -vserver <svm> -policyname <name> -clientmatch <cidr> -rorule sys -rwrule sys` — add a client access rule to an export policy
- `cifs share create -vserver <svm> -share-name <name> -path <path>` — create a CIFS share

# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

function find_files_with_missing {
    files=$(find . -name "*.$1" | grep -v node_modules | grep -v cdk.out )
    egrep -l 'Copyright 202[0-3] Amazon' $files  | sort > .c1
    echo $files | tr ' ' '\n' | sort > .c2
    comm -3 .c1 .c2
    rm .c1 .c2
}


find_files_with_missing "py" | while read fn ; do 
    cat py.copy $fn > tmp
    mv tmp $fn
done

find_files_with_missing "ts" | while read fn ; do 
    cat ts.copy $fn > tmp
    mv tmp $fn
done

find_files_with_missing "tsx" | while read fn ; do 
    cat ts.copy $fn > tmp
    mv tmp $fn
done
